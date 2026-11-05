#!/usr/bin/env python3
"""Get a real Keycloak access token for a demo user or the operator tooling client (the auth compose profile).

    python scripts/keycloak_token.py customer              # or operator, admin: authorization code + PKCE
    python scripts/keycloak_token.py tooling               # client credentials, the confidential client
    python scripts/keycloak_token.py operator --claims     # prints the decoded claims instead of the token
    TOKEN=$(python scripts/keycloak_token.py customer) && curl -H "Authorization: Bearer $TOKEN" ...

Demo users log in exactly as a browser would: the public client's authorization code flow with PKCE, submitting
Keycloak's own login form. The realm has no password grant to take a shortcut through. Credentials come from
secrets/keycloak.env (scripts/keycloak_secrets.py). Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keycloak_secrets import read_env_file

BASE_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8180")
REALM_URL = f"{BASE_URL}/realms/smart-tours/protocol/openid-connect"
PUBLIC_CLIENT = "smart-tours-web"
TOOLING_CLIENT = "smart-tours-operator-tooling"
REDIRECT_URI = os.environ.get("KEYCLOAK_WEB_ORIGIN", "http://localhost:3000") + "/callback"
PASSWORD_KEYS = {
    "customer": "KEYCLOAK_DEMO_CUSTOMER_PASSWORD",
    "operator": "KEYCLOAK_DEMO_OPERATOR_PASSWORD",
    "admin": "KEYCLOAK_DEMO_ADMIN_PASSWORD",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The login's last redirect goes to the front end, which isn't running: the code is read from it instead."""

    def redirect_request(self, *args, **kwargs):
        return None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token_request(form: dict[str, str]) -> str:
    request = urllib.request.Request(f"{REALM_URL}/token", data=urllib.parse.urlencode(form).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as error:
        raise SystemExit(f"token request failed: {error.code} {error.read().decode(errors='replace')}") from error


def user_token(user: str, password: str) -> str:
    verifier = _b64url(secrets.token_bytes(48))
    query = urllib.parse.urlencode({
        "client_id": PUBLIC_CLIENT,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": REDIRECT_URI,
        "state": secrets.token_urlsafe(16),
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    })
    # Keycloak marks its login cookies Secure. Browsers send those to localhost over plain HTTP; cookiejar has to be
    # told to, and only for a local Keycloak: anywhere else, that URL should be HTTPS anyway.
    local = urllib.parse.urlparse(BASE_URL).hostname in ("localhost", "127.0.0.1")
    policy = http.cookiejar.DefaultCookiePolicy(secure_protocols=("https", "wss", "http") if local else ("https", "wss"))
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar(policy)), _NoRedirect()
    )
    with opener.open(f"{REALM_URL}/auth?{query}", timeout=30) as response:
        page = response.read().decode()
    form_action = re.search(r'<form[^>]*action="([^"]+)"', page)
    if not form_action:
        raise SystemExit("no login form in Keycloak's answer: is the client configured for this redirect URI?")
    credentials = urllib.parse.urlencode({"username": f"demo-{user}", "password": password, "credentialId": ""})
    try:
        with opener.open(html.unescape(form_action.group(1)), data=credentials.encode(), timeout=30):
            raise SystemExit(f"login failed for demo-{user}: wrong password, or the account is temporarily locked")
    except urllib.error.HTTPError as redirect:
        if redirect.code != 302:
            raise SystemExit(f"login failed for demo-{user}: HTTP {redirect.code}") from redirect
        location = redirect.headers["Location"]
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code")
    if not code:
        raise SystemExit(f"no code in the login's redirect for demo-{user}")
    return _token_request({
        "grant_type": "authorization_code",
        "client_id": PUBLIC_CLIENT,
        "code": code[0],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


def tooling_token(secret: str) -> str:
    return _token_request({"grant_type": "client_credentials", "client_id": TOOLING_CLIENT, "client_secret": secret})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("who", choices=[*PASSWORD_KEYS, "tooling"])
    parser.add_argument("--claims", action="store_true", help="print the decoded claims, not the token")
    args = parser.parse_args()

    env = read_env_file()
    key = "KEYCLOAK_OPERATOR_TOOLING_SECRET" if args.who == "tooling" else PASSWORD_KEYS[args.who]
    if key not in env:
        raise SystemExit(f"{key} not found: run python scripts/keycloak_secrets.py first")
    token = tooling_token(env[key]) if args.who == "tooling" else user_token(args.who, env[key])
    if args.claims:
        payload = token.split(".")[1]
        print(json.dumps(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))), indent=2))
    else:
        print(token)


if __name__ == "__main__":
    main()
