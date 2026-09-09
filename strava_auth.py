#!/usr/bin/env python
"""One-time Strava OAuth2 setup.

Run this once:  python strava_auth.py

It walks you through registering an API app (if you haven't), opens the Strava
consent page in your browser, catches the redirect on localhost, exchanges the
code for tokens, and writes them to .env (gitignored). Tokens are never printed.
"""

from __future__ import annotations

import secrets
import sys
import threading
import time
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from gearcheck import env_store

PORT = 8721
REDIRECT_URI = f"http://localhost:{PORT}/callback"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:read_all,profile:read_all"

REGISTRATION_HELP = f"""
You need a Strava API application. It takes about a minute:

  1. Open  https://www.strava.com/settings/api
  2. Fill in the form:
       Application Name          anything, e.g. "Gear Check"
       Category                  Data Importer
       Website                   http://localhost
       Authorization Callback Domain   localhost      <-- must be exactly this
  3. Agree to the API terms and create it.
  4. The page then shows your Client ID and Client Secret.

The callback domain must be "localhost" (no port, no scheme) so that this
script's redirect to {REDIRECT_URI} is accepted.
"""


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None
    error: str | None = None
    scope: str = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        # Browsers also request /favicon.ico and friends; only the callback counts.
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        if "code" in query:
            _CallbackHandler.code = query["code"][0]
            _CallbackHandler.state = query.get("state", [""])[0]
            _CallbackHandler.scope = query.get("scope", [""])[0]
            body = "<h2>Authorised.</h2><p>You can close this tab and return to the terminal.</p>"
        else:
            _CallbackHandler.error = query.get("error", ["unknown_error"])[0]
            body = f"<h2>Authorisation failed</h2><p>{escape(_CallbackHandler.error)}</p>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: object) -> None:
        pass  # keep the redirect (which carries the code) out of the console


def _prompt_credentials() -> tuple[str, str]:
    env_store.load()
    client_id = env_store.get("STRAVA_CLIENT_ID")
    client_secret = env_store.get("STRAVA_CLIENT_SECRET")

    if client_id and client_secret:
        print(f"Using Strava app client_id={client_id} (secret {env_store.mask(client_secret)})")
        if input("Use this app? [Y/n] ").strip().lower() in {"n", "no"}:
            client_id = client_secret = None

    if not (client_id and client_secret):
        print(REGISTRATION_HELP)
        if input("Open the Strava API settings page now? [Y/n] ").strip().lower() not in {"n", "no"}:
            webbrowser.open("https://www.strava.com/settings/api")
        client_id = input("\n  Client ID: ").strip()
        client_secret = input("  Client Secret: ").strip()
        if not client_id or not client_secret:
            sys.exit("Both Client ID and Client Secret are required.")
        env_store.set_values(
            {"STRAVA_CLIENT_ID": client_id, "STRAVA_CLIENT_SECRET": client_secret}
        )
        print(f"Saved to {env_store.ENV_PATH.name} (gitignored).")

    return client_id, client_secret


def main() -> None:
    client_id, client_secret = _prompt_credentials()
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": state,
    }
    url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    try:
        server = HTTPServer(("localhost", PORT), _CallbackHandler)
    except OSError as exc:
        sys.exit(
            f"Could not listen on localhost:{PORT} ({exc}). "
            "Something else is using that port — close it and re-run."
        )
    server.timeout = 0.5

    def serve_until_done(deadline: float) -> None:
        while time.monotonic() < deadline:
            if _CallbackHandler.code or _CallbackHandler.error:
                return
            server.handle_request()  # returns after server.timeout if idle

    thread = threading.Thread(target=serve_until_done, args=(time.monotonic() + 300,), daemon=True)
    thread.start()

    print(f"\nOpening Strava consent page. If it doesn't open, paste this in a browser:\n{url}\n")
    webbrowser.open(url)
    print(f"Waiting for the redirect to {REDIRECT_URI} ... (Ctrl+C to give up)")
    thread.join(timeout=305)
    server.server_close()

    if _CallbackHandler.error:
        sys.exit(f"Strava returned an error: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        sys.exit("Timed out waiting for the Strava redirect. Re-run to try again.")
    if _CallbackHandler.state != state:
        sys.exit("State mismatch on the OAuth callback — aborting.")

    granted = set(_CallbackHandler.scope.split(","))
    if "activity:read_all" not in granted:
        sys.exit(
            "The 'activity:read_all' scope was not granted, so private activities "
            "would be invisible. Re-run and tick all the boxes on the consent screen."
        )

    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _CallbackHandler.code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if response.status_code != 200:
        sys.exit(f"Token exchange failed ({response.status_code}).")

    payload = response.json()
    env_store.set_values(
        {
            "STRAVA_ACCESS_TOKEN": payload["access_token"],
            "STRAVA_REFRESH_TOKEN": payload["refresh_token"],
            "STRAVA_EXPIRES_AT": str(payload["expires_at"]),
        }
    )
    athlete = payload.get("athlete") or {}
    who = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    print(f"\nAuthorised{f' as {who}' if who else ''}. Tokens written to {env_store.ENV_PATH.name}.")
    print("Scopes granted:", _CallbackHandler.scope)
    print("\nNext:  python compare_gear.py --days 90")


if __name__ == "__main__":
    main()
