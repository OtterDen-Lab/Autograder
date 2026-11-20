"""Manifest-only registry lookups, without a local Docker daemon."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests

from Autograder.exceptions import ImageBuildError


class RegistryClient:
  def __init__(self, scheme='https', verify=True, auth_file=None, session=None):
    self.scheme = scheme
    self.verify = str(Path(verify).expanduser()) if isinstance(verify, str) else verify
    self.auth_file = auth_file
    self.session = session if session is not None else requests.Session()

  def docker_config(self):
    if not self.auth_file:
      return '{}'
    raw = Path(self.auth_file).expanduser().read_text()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('auths', {}), dict):
      raise ImageBuildError('Registry auth file must be a Docker config.json mapping')
    if data.get('credsStore') or data.get('credHelpers'):
      raise ImageBuildError('Remote builds need a Docker auth file with inline auths; '
                            'local credential helpers are not available inside the build Job')
    return raw

  def find(self, image):
    host, name = image.split('/', 1)
    repository, tag = name.rsplit(':', 1)
    config = json.loads(self.docker_config())
    entries = config.get('auths', {})
    entry = entries.get(host) or entries.get('https://' + host) or entries.get('http://' + host) or {}
    auth = None
    if entry.get('auth'):
      try:
        user, password = base64.b64decode(entry['auth'], validate=True).decode().split(':', 1)
        auth = (user, password)
      except (ValueError, UnicodeError):
        raise ImageBuildError('Registry auth file contains an invalid auth entry') from None
    if entry.get('identitytoken'):
      raise ImageBuildError('Registry identitytoken login is not supported; use inline username/password auth')
    url = f'{self.scheme}://{host}/v2/{repository}/manifests/{tag}'
    headers = {'Accept': ', '.join([
      'application/vnd.oci.image.manifest.v1+json',
      'application/vnd.oci.image.index.v1+json',
      'application/vnd.docker.distribution.manifest.v2+json',
      'application/vnd.docker.distribution.manifest.list.v2+json'])}
    try:
      response = self.session.get(url, headers=headers, auth=auth, verify=self.verify,
                                   timeout=(5, 30), allow_redirects=False)
      if response.status_code == 401:
        challenge = response.headers.get('WWW-Authenticate', '')
        response.close()
        if not challenge.lower().startswith('bearer '):
          raise ImageBuildError('Registry lookup denied (HTTP 401); check registry_auth_file')
        params = requests.utils.parse_dict_header(challenge[7:])
        realm = params.get('realm', '')
        if urlsplit(realm).scheme != 'https' and not (
            self.scheme == 'http' and urlsplit(realm).scheme == 'http'):
          raise ImageBuildError('Registry returned an unsupported authentication endpoint')
        query = {'scope': params.get('scope', f'repository:{repository}:pull')}
        if params.get('service'):
          query['service'] = params['service']
        token_response = self.session.get(realm, params=query, auth=auth,
          verify=self.verify, timeout=(5, 30), allow_redirects=False)
        try:
          if token_response.status_code != 200:
            raise ImageBuildError(f'Registry token request failed (HTTP {token_response.status_code})')
          token_data = token_response.json()
          token = token_data.get('token') or token_data.get('access_token')
          if not isinstance(token, str) or not token:
            raise ImageBuildError('Registry returned no authentication token')
        finally:
          token_response.close()
        headers['Authorization'] = 'Bearer ' + token
        response = self.session.get(url, headers=headers, verify=self.verify,
                                     timeout=(5, 30), allow_redirects=False)
      try:
        try:
          data = response.json()
        except ValueError:
          data = {}
        if response.status_code == 200:
          if not isinstance(data, dict) or data.get('schemaVersion') != 2:
            raise ImageBuildError('Registry returned an invalid image manifest')
          return image
        codes = {item.get('code') for item in data.get('errors', []) if isinstance(item, dict)} if isinstance(data, dict) else set()
        if response.status_code == 404 and codes and codes <= {'MANIFEST_UNKNOWN', 'NAME_UNKNOWN'}:
          return None
        raise ImageBuildError(f'Registry manifest lookup failed (HTTP {response.status_code}); '
                              'check registry access, authentication, and proxy configuration')
      finally:
        response.close()
    except requests.RequestException:
      # Request exception strings can contain URLs/credentials; do not echo them.
      raise ImageBuildError('Registry manifest lookup failed; check connectivity and TLS trust') from None
