"""Panopto OAuth refresh-token bootstrap helper."""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import dotenv

from Autograder.external_tools import (
  build_panopto_authorization_url,
  request_panopto_authorization_code_token,
  save_panopto_refresh_token,
)

DEFAULT_ENV_PATH = "~/.tokens/autograder.env"
DEFAULT_REFRESH_TOKEN_PATH = "~/.tokens/autograder.panopto.json"


class _CodeCapture:
  def __init__(self):
    self.code = None
    self.state = None
    self.error = None
    self.event = threading.Event()


def _make_handler(capture: _CodeCapture):
  class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
      parsed = urllib.parse.urlparse(self.path)
      params = urllib.parse.parse_qs(parsed.query)
      capture.code = params.get("code", [None])[0]
      capture.state = params.get("state", [None])[0]
      capture.error = params.get("error", [None])[0]
      capture.event.set()

      self.send_response(200)
      self.send_header("Content-Type", "text/plain; charset=utf-8")
      self.end_headers()
      if capture.error:
        self.wfile.write(
          f"Panopto authorization failed: {capture.error}\n".encode("utf-8"))
      else:
        self.wfile.write(
          b"Panopto authorization complete. You can close this tab.\n")

    def log_message(self, format, *args):  # noqa: A003
      return

  return Handler


def add_refresh_panopto_token_arguments(parser: argparse.ArgumentParser,
                                        include_env: bool = False) -> None:
  """Add refresh-token options to a CLI parser."""
  if include_env:
    parser.add_argument("--env",
                        default=argparse.SUPPRESS,
                        help="Path to the credential env file")
  parser.add_argument("--client-id")
  parser.add_argument("--client-secret")
  parser.add_argument("--base-url")
  parser.add_argument("--scope")
  parser.add_argument("--token-url")
  parser.add_argument("--authorize-url")
  parser.add_argument("--redirect-uri")
  parser.add_argument("--output")
  parser.add_argument("--port", type=int, default=8765)


def refresh_panopto_token(args: argparse.Namespace) -> int:
  """Run the browser OAuth flow and save Panopto's refresh token."""
  env_path = getattr(args, "env", DEFAULT_ENV_PATH)
  dotenv.load_dotenv(os.path.expanduser(env_path))
  client_id = args.client_id or os.getenv("PANOPTO_CLIENT_ID")
  client_secret = args.client_secret or os.getenv("PANOPTO_CLIENT_SECRET")
  if not client_id or not client_secret:
    raise SystemExit(
      "client id/secret are required. Set PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET or pass --client-id/--client-secret."
    )

  base_url = (args.base_url or os.getenv(
    "PANOPTO_BASE_URL", "https://csumb.hosted.panopto.com"))
  scope = args.scope or os.getenv("PANOPTO_SCOPE", "api")
  redirect_uri = args.redirect_uri or os.getenv(
    "PANOPTO_REDIRECT_URI", "http://127.0.0.1:8765/callback")
  authorize_url = args.authorize_url or os.getenv("PANOPTO_AUTHORIZE_URL")
  if not authorize_url:
    authorize_url = f"{base_url.rstrip('/')}/Panopto/oauth2/connect/authorize"
  token_url = args.token_url or os.getenv("PANOPTO_TOKEN_URL")
  if not token_url:
    token_url = f"{base_url.rstrip('/')}/Panopto/oauth2/connect/token"
  output = args.output or os.getenv("PANOPTO_REFRESH_TOKEN_PATH",
                                    DEFAULT_REFRESH_TOKEN_PATH)

  bootstrap_scope = scope.split()
  for extra_scope in ("openid", "offline_access"):
    if extra_scope not in bootstrap_scope:
      bootstrap_scope.append(extra_scope)
  requested_scope = " ".join(bootstrap_scope)

  capture = _CodeCapture()
  server = HTTPServer(("127.0.0.1", args.port), _make_handler(capture))
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()

  auth_url = build_panopto_authorization_url(
    authorize_url=authorize_url,
    client_id=client_id,
    redirect_uri=redirect_uri,
    scope=requested_scope,
  )
  print(auth_url)
  print(
    f"Open that URL in a browser, log in, and wait for the callback to {redirect_uri}"
  )

  try:
    if not capture.event.wait(timeout=600):
      raise SystemExit("Timed out waiting for the Panopto callback")
    if capture.error:
      raise SystemExit(f"Panopto returned error={capture.error}")
    if not capture.code:
      raise SystemExit("Panopto callback did not include an authorization code")

    token_bundle = request_panopto_authorization_code_token(
      client_id=client_id,
      client_secret=client_secret,
      authorization_code=capture.code,
      redirect_uri=redirect_uri,
      token_url=token_url,
      scope=requested_scope,
    )
    if not token_bundle.refresh_token:
      raise SystemExit(
        "Panopto did not return a refresh token. Check that the client type supports refresh tokens and that the requested scope is allowed."
      )
    save_panopto_refresh_token(output, token_bundle.refresh_token)
    print(json.dumps({
      "access_token": token_bundle.access_token,
      "refresh_token": token_bundle.refresh_token,
      "expires_in": token_bundle.expires_in,
      "token_type": token_bundle.token_type,
      "output": os.path.abspath(os.path.expanduser(output)),
    }, indent=2))
    return 0
  finally:
    server.shutdown()
    server.server_close()


def main(argv: list[str] | None = None) -> int:
  """Run the helper as a standalone script."""
  parser = argparse.ArgumentParser(
    description="Bootstrap a Panopto refresh token using a one-time browser login"
  )
  add_refresh_panopto_token_arguments(parser, include_env=True)
  return refresh_panopto_token(parser.parse_args(argv))
