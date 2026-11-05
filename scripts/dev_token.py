#!/usr/bin/env python3
"""Mint a short-lived HS256 JWT for local development.

Signs one with the same JWT_SECRET the gateway verifies against, for the default profile, which runs without
Keycloak. The gateway accepts these only with DEV_TOKENS_ENABLED=true (ADR 0016); for real tokens from Keycloak see
scripts/keycloak_token.py. Standard library only, so it runs without a virtualenv.

    python scripts/dev_token.py                         # CUSTOMER, sub=dev-user
    python scripts/dev_token.py --sub alice --roles CUSTOMER OPERATOR
    python scripts/dev_token.py --sub alice --email alice@example.com   # email claim: booking notifications
    TOKEN=$(python scripts/dev_token.py) && curl -H "Authorization: Bearer $TOKEN" ...

JWT_SECRET is read from the environment, falling back to the repo's .env file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _secret_from_env_file() -> str | None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip()
    return None


def mint(secret_b64: str, sub: str, roles: list[str], ttl_seconds: int, email: str | None = None) -> str:
    key = base64.b64decode(secret_b64)
    if len(key) < 32:
        raise SystemExit("JWT_SECRET must decode to at least 32 bytes (use: openssl rand -base64 48)")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": "smart-tours-dev",
        "aud": "smart-tours-api",
        "sub": sub,
        "roles": roles,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if email:
        # Keycloak puts the user's address here too; the gateway forwards it as X-User-Email.
        claims["email"] = email
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = hmac.new(key, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sub", default="dev-user", help="user id placed in the 'sub' claim")
    parser.add_argument("--roles", nargs="+", default=["CUSTOMER"], help="CUSTOMER, OPERATOR and/or ADMIN")
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    parser.add_argument("--email", help="'email' claim: where booking notifications go (none: no notifications)")
    args = parser.parse_args()

    secret = os.environ.get("JWT_SECRET") or _secret_from_env_file()
    if not secret:
        sys.exit("JWT_SECRET not set and no .env file found (cp .env.example .env)")
    print(mint(secret, args.sub, args.roles, args.ttl, args.email))


if __name__ == "__main__":
    main()
