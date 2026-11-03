#!/usr/bin/env python3
"""Generate the Ed25519 key pair that signs ticket check-in tokens (ADR 0015). Run once per environment.

    python scripts/ticket_keys.py            # writes secrets/ (git-ignored); refuses to overwrite
    python scripts/ticket_keys.py --rotate   # new pair; tickets signed with the old key stop verifying

    secrets/ticket-signing/ticket_signing_key.pem   private: only notification-service's worker mounts it
    secrets/ticket-verify/ticket_verify_key.pem     public: booking-service verifies check-ins with it

Needs the `cryptography` package (pip install cryptography); every service's requirements already include it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "secrets" / "ticket-signing" / "ticket_signing_key.pem"
PUBLIC = ROOT / "secrets" / "ticket-verify" / "ticket_verify_key.pem"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rotate", action="store_true", help="replace an existing key pair")
    args = parser.parse_args()
    if PRIVATE.exists() and not args.rotate:
        raise SystemExit(f"{PRIVATE.relative_to(ROOT)} exists; use --rotate to replace it")

    key = Ed25519PrivateKey.generate()
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    # Readable by the container user (compose mounts it read-only); keep the host copy as private as the OS allows.
    os.chmod(PRIVATE, 0o644)
    PUBLIC.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    print(f"wrote {PRIVATE.relative_to(ROOT)} and {PUBLIC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
