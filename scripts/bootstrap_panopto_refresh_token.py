#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from Autograder.external_tools import (
  build_panopto_authorization_url,
  request_panopto_authorization_code_token,
  save_panopto_refresh_token,
)


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


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Bootstrap a Panopto refresh token using a one-time browser login"
  )
  parser.add_argument("--client-id", default=os.getenv("PANOPTO_CLIENT_ID"))
  parser.add_argument("--client-secret",
                      default=os.getenv("PANOPTO_CLIENT_SECRET"))
  parser.add_argument("--base-url",
                      default=os.getenv("PANOPTO_BASE_URL",
                                        "https://csumb.hosted.panopto.com"))
  parser.add_argument("--scope", default=os.getenv("PANOPTO_SCOPE", "api"))
  parser.add_argument("--token-url",
                      default=os.getenv(
                        "PANOPTO_TOKEN_URL",
                        None))
  parser.add_argument("--authorize-url",
                      default=os.getenv(
                        "PANOPTO_AUTHORIZE_URL",
                        None))
  parser.add_argument("--redirect-uri",
                      default=os.getenv("PANOPTO_REDIRECT_URI",
                                        "http://127.0.0.1:8765/callback"))
  parser.add_argument("--output",
                      default=os.getenv("PANOPTO_REFRESH_TOKEN_PATH",
                                        "~/.autograder/panopto_refresh_token.json"))
  parser.add_argument("--port", type=int, default=8765)
  args = parser.parse_args()

  if not args.client_id or not args.client_secret:
    raise SystemExit(
      "client id/secret are required. Set PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET or pass --client-id/--client-secret."
    )

  authorize_url = args.authorize_url
  if not authorize_url:
    authorize_url = f"{args.base_url.rstrip('/')}/Panopto/oauth2/connect/authorize"

  token_url = args.token_url
  if not token_url:
    token_url = f"{args.base_url.rstrip('/')}/Panopto/oauth2/connect/token"

  bootstrap_scope = args.scope.split()
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
    client_id=args.client_id,
    redirect_uri=args.redirect_uri,
    scope=requested_scope,
  )
  print(auth_url)
  print(
    f"Open that URL in a browser, log in, and wait for the callback to {args.redirect_uri}"
  )

  try:
    if not capture.event.wait(timeout=600):
      raise SystemExit("Timed out waiting for the Panopto callback")
    if capture.error:
      raise SystemExit(f"Panopto returned error={capture.error}")
    if not capture.code:
      raise SystemExit("Panopto callback did not include an authorization code")

    token_bundle = request_panopto_authorization_code_token(
      client_id=args.client_id,
      client_secret=args.client_secret,
      authorization_code=capture.code,
      redirect_uri=args.redirect_uri,
      token_url=token_url,
      scope=requested_scope,
    )
    if not token_bundle.refresh_token:
      raise SystemExit(
        "Panopto did not return a refresh token. Check that the client type supports refresh tokens and that the requested scope is allowed."
      )
    save_panopto_refresh_token(args.output, token_bundle.refresh_token)
    print(json.dumps({
      "access_token": token_bundle.access_token,
      "refresh_token": token_bundle.refresh_token,
      "expires_in": token_bundle.expires_in,
      "token_type": token_bundle.token_type,
      "output": os.path.abspath(os.path.expanduser(args.output)),
    }, indent=2))
    return 0
  finally:
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
  raise SystemExit(main())
