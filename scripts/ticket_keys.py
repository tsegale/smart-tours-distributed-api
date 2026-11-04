#!/usr/bin/env python3
"""The Ed25519 keys that sign ticket check-in tokens (ADR 0015), and their rotation.

    python scripts/ticket_keys.py              # first run: a signing key, and its public half published
    python scripts/ticket_keys.py --list       # the published keys, which one signs, and nothing secret
    python scripts/ticket_keys.py --rotate     # a new signing key; the previous public key stays published
    python scripts/ticket_keys.py --retire KID # unpublish a previous key once its last ticket has expired

    secrets/ticket-signing/ticket_signing_key.pem   private, the active key: only the render worker mounts it
    secrets/ticket-public/<kid>.pem                 public: notification-service publishes them as a JWKS and
                                                    booking-service verifies a check-in by the token's kid

A key's id (kid) is its RFC 7638 thumbprint, as notification-service computes it. Run without arguments on an
existing key, the script only publishes that key's public half if it isn't yet (safe to repeat). The procedure,
including how long to keep a retired key: docs/runbooks/ticket-key-rotation.md.

Needs the `cryptography` package (pip install cryptography); every service's requirements already include it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "secrets" / "ticket-signing" / "ticket_signing_key.pem"
PUBLIC_DIR = ROOT / "secrets" / "ticket-public"


def key_id(key: Ed25519PublicKey) -> str:
    """RFC 7638 thumbprint of the key's JWK (RFC 8037); the same as notification-service's app.tickets.key_id."""
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    canonical = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).rstrip(b"=").decode()


def load_private() -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(PRIVATE.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{PRIVATE.relative_to(ROOT)} is not an Ed25519 private key")
    return key


def write_private(key: Ed25519PrivateKey) -> None:
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRIVATE.with_suffix(".pem.new")
    temporary.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    # Readable by the container user (compose mounts it read-only); keep the host copy as private as the OS allows.
    os.chmod(temporary, 0o644)
    temporary.replace(PRIVATE)  # atomic: a worker restarting mid-rotation reads the old key or the new one


def publish(key: Ed25519PublicKey) -> str:
    kid = key_id(key)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    path = PUBLIC_DIR / f"{kid}.pem"
    if not path.exists():
        path.write_bytes(key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        print(f"published {path.relative_to(ROOT)}")
    return kid


def published() -> dict[str, Path]:
    keys = {}
    for path in sorted(PUBLIC_DIR.glob("*.pem")):
        key = serialization.load_pem_public_key(path.read_bytes())
        if isinstance(key, Ed25519PublicKey):
            keys[key_id(key)] = path
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--rotate", action="store_true", help="new signing key; the previous one stays published")
    action.add_argument("--retire", metavar="KID", help="unpublish a previous key (never the active one)")
    action.add_argument("--list", action="store_true", help="show the published keys and the active one")
    args = parser.parse_args()

    active = key_id(load_private().public_key()) if PRIVATE.exists() else None
    if args.list:
        for kid in published():
            print(f"{kid}  {'active (signs new tickets)' if kid == active else 'retired, still accepted'}")
        if active and active not in published():
            print(f"{active}  active but NOT published: run this script without arguments")
        return
    if args.retire:
        if args.retire == active:
            raise SystemExit(f"{args.retire} is the active signing key; rotate first")
        path = published().get(args.retire)
        if path is None:
            raise SystemExit(f"{args.retire} is not published")
        path.unlink()
        print(f"retired {args.retire}: booking-service stops accepting its tickets within its JWKS cache TTL")
        return
    if args.rotate:
        if active is None:
            raise SystemExit(f"{PRIVATE.relative_to(ROOT)} does not exist; run without --rotate first")
        key = Ed25519PrivateKey.generate()
        new = publish(key.public_key())  # published before it signs anything
        write_private(key)
        print(f"rotated: {new} signs new tickets; {active} stays published until you retire it")
        return
    if active is None:
        key = Ed25519PrivateKey.generate()
        publish(key.public_key())
        write_private(key)
        print(f"wrote {PRIVATE.relative_to(ROOT)}")
    else:
        publish(load_private().public_key())
    print(f"active key: {key_id(load_private().public_key())}")


if __name__ == "__main__":
    main()
