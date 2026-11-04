"""PDF e-tickets: a signed, expiring check-in token in a QR code, rendered with Jinja2 + WeasyPrint (ADR 0015).

The token is a compact JWS (EdDSA, Ed25519) signed with a key of its own, not the gateway's auth secret: whoever
holds this key can mint tickets, so it lives only here; booking-service verifies with the public half and can't.

Keys rotate (docs/runbooks/ticket-key-rotation.md). Each key's id (`kid`) is its RFC 7638 JWK thumbprint, so an id
can never point at the wrong key. The public keys to accept are files in one directory, the active key's and those
of retired keys whose tickets haven't expired yet; the API publishes them as a JWKS, and booking-service picks the
key by the token's `kid`.
"""

import base64
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import jwt
import qrcode
import qrcode.image.svg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jinja2 import Environment, FileSystemLoader

from .models import BookingSnapshot

ISSUER = "smarttours-notifications"
AUDIENCE = "smarttours-checkin"
TOKEN_TYPE = "checkin+jwt"
ALGORITHM = "EdDSA"

# HTML templates (*.html.j2) are escaped; plain-text mail (*.txt.j2) is not, or "&" would arrive as "&amp;".
TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=lambda name: name is not None and name.endswith(".html.j2"),
)


@dataclass(frozen=True)
class CheckInToken:
    value: str
    expires_at: datetime
    key_id: str


class KeyNotPublishedError(RuntimeError):
    """The signing key's public half is not in the published key set: its tickets could not be verified."""


@lru_cache
def signing_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def public_jwk(key: Ed25519PublicKey) -> dict[str, str]:
    """The key as a JWK (RFC 8037), with its thumbprint as kid."""
    x = _b64url(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    # RFC 7638: the required members only, in lexicographic order, no whitespace.
    thumbprint = _b64url(hashlib.sha256(json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x},
                                                   separators=(",", ":")).encode()).digest())  # fmt: skip
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": thumbprint, "use": "sig", "alg": ALGORITHM}


def key_id(key: Ed25519PublicKey) -> str:
    return public_jwk(key)["kid"]


def published_keys(directory: Path) -> list[dict[str, str]]:
    """Every public key in the directory (*.pem) as a JWK, sorted by kid. Not cached: rotation must not need a
    restart, and the directory holds a handful of small files."""
    keys = []
    for path in sorted(directory.glob("*.pem")):
        key = serialization.load_pem_public_key(path.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError(f"{path} is not an Ed25519 public key")
        keys.append(public_jwk(key))
    return sorted(keys, key=lambda jwk: jwk["kid"])


def ensure_published(key: Ed25519PrivateKey, directory: Path) -> str:
    """The signing key's kid, once its public half is published; a ticket nobody can verify is never issued."""
    kid = key_id(key.public_key())
    if kid not in {jwk["kid"] for jwk in published_keys(directory)}:
        raise KeyNotPublishedError(
            f"the ticket signing key {kid} is not in {directory}; run scripts/ticket_keys.py to publish it"
        )
    return kid


def issue_token(
    booking: BookingSnapshot, key: Ed25519PrivateKey, grace: timedelta, now: datetime | None = None
) -> CheckInToken:
    """The booking and its departure, valid from now until `grace` after departure, with the key's kid."""
    now = now or datetime.now(UTC)
    expires_at = booking.starts_at.astimezone(UTC) + grace
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(booking.booking_id),
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "ref": booking.reference,
        "aid": booking.availability_id,
        "dep": booking.starts_at.astimezone(UTC).isoformat(),
    }
    kid = key_id(key.public_key())
    value = jwt.encode(claims, key, algorithm=ALGORITHM, headers={"kid": kid, "typ": TOKEN_TYPE})
    return CheckInToken(value, expires_at, kid)


def qr_svg_data_uri(text: str) -> str:
    """The token as a QR code, SVG so it stays sharp at any print size. Error correction M survives a crease."""
    image = qrcode.make(
        text, image_factory=qrcode.image.svg.SvgPathImage, error_correction=qrcode.constants.ERROR_CORRECT_M
    )
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def local_departure(booking: BookingSnapshot) -> datetime:
    return booking.starts_at.astimezone(ZoneInfo(booking.time_zone))


def render_pdf(booking: BookingSnapshot, token: CheckInToken) -> bytes:
    from weasyprint import HTML  # heavy import (Pango): only in the processes that render

    html = TEMPLATES.get_template("ticket.html.j2").render(
        booking=booking,
        departure=local_departure(booking),
        qr=qr_svg_data_uri(token.value),
        expires_at=token.expires_at.astimezone(ZoneInfo(booking.time_zone)),
    )
    return HTML(string=html).write_pdf()


def object_key(booking: BookingSnapshot) -> str:
    return f"tickets/{booking.booking_id}/{booking.reference}.pdf"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
