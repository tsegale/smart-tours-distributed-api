#!/usr/bin/env python3
"""Generate the auth profile's secrets once, into secrets/ (git-ignored). Prints no values.

    python scripts/keycloak_secrets.py

secrets/keycloak.env holds the Keycloak admin login, its database password, the operator tooling client's secret and
one password per demo user. The realm file refers to these by name (infra/keycloak/realm/smart-tours-realm.json), so
none of them is in git. secrets/keycloak-db.env holds only the database's password, for the database's container.

Running it again keeps existing values and only adds missing ones: a realm is imported once, so changing a password
here later would not change it in Keycloak. Standard library only.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
ENV_FILE = SECRETS_DIR / "keycloak.env"
DB_ENV_FILE = SECRETS_DIR / "keycloak-db.env"
GENERATED = (
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
    "KC_DB_PASSWORD",
    "KEYCLOAK_OPERATOR_TOOLING_SECRET",
    "KEYCLOAK_DEMO_CUSTOMER_PASSWORD",
    "KEYCLOAK_DEMO_OPERATOR_PASSWORD",
    "KEYCLOAK_DEMO_ADMIN_PASSWORD",
)


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def _write(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8", newline="\n")
    os.chmod(path, 0o600)


def main() -> None:
    values = read_env_file()
    added = [key for key in GENERATED if key not in values]
    for key in added:
        values[key] = secrets.token_hex(24)  # hex: nothing compose or a shell would interpret
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    _write(ENV_FILE, values)
    _write(DB_ENV_FILE, {"POSTGRES_PASSWORD": values["KC_DB_PASSWORD"]})
    print(f"secrets/keycloak.env: {len(added)} added, {len(values) - len(added)} kept")


if __name__ == "__main__":
    main()
