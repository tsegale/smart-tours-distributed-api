"""Tickets and check-in (ADR 0015).

- Download: the owner gets a 302 to a presigned URL for the PDF in object storage, valid for a few minutes and
  signed by a read-only user. The bucket stays private; the URL is the only way in, and it expires.
- Check-in: the QR code holds a compact JWS (EdDSA) that notification-service signed. booking-service holds only the
  public key, so it can verify tickets but not mint them. A scan is accepted once, on the day of departure in the
  tour's time zone, for a confirmed booking whose reference and departure match the token.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import boto3
import jwt
from botocore.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import (
    AlreadyCheckedInError,
    BookingNotConfirmedError,
    NotFoundError,
    TicketExpiredError,
    TicketInvalidError,
    TicketNotReadyError,
    TicketsUnavailableError,
    WrongDayError,
)
from ..models import Booking, BookingStatus, BookingTicket

log = logging.getLogger(__name__)

ISSUER = "smarttours-notifications"
AUDIENCE = "smarttours-checkin"
ALGORITHM = "EdDSA"


# ---------------------------------------------------------------- download


def presigned_ticket_url(db: Session, booking: Booking, settings: Settings) -> str:
    ticket = db.get(BookingTicket, booking.id)
    if ticket is None:
        raise TicketNotReadyError("The ticket for this booking has not been issued yet", booking_id=booking.id)
    if not settings.tickets_s3_secret_key:
        raise TicketsUnavailableError("Ticket downloads are not configured")
    return _presigner(settings.tickets_s3_public_endpoint_url, settings.tickets_s3_region,
                      settings.tickets_s3_access_key, settings.tickets_s3_secret_key).generate_presigned_url(
        "get_object",
        Params={"Bucket": ticket.bucket, "Key": ticket.object_key},
        ExpiresIn=settings.ticket_url_ttl_s,
    )  # fmt: skip


@lru_cache
def _presigner(endpoint: str, region: str, access_key: str, secret_key: str):
    """Signs locally, no network call. The endpoint is the one the browser uses: the signature covers the host."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def record_ticket_rendered(db: Session, event: dict[str, Any]) -> bool:
    """Store where a booking's ticket is (from ticket.rendered). Idempotent; a re-render replaces the older one."""
    if event.get("schemaVersion") != 1:
        raise ValueError(f"ticket.rendered schemaVersion {event.get('schemaVersion')} is not supported")
    values = {
        "booking_id": int(event["bookingId"]),
        "bucket": str(event["bucket"]),
        "object_key": str(event["objectKey"]),
        "sha256": str(event["sha256"]),
        "size_bytes": int(event["sizeBytes"]),
        "event_id": uuid.UUID(event["eventId"]),
        "rendered_at": datetime.fromisoformat(event["occurredAt"]),
    }
    if db.get(Booking, values["booking_id"]) is None:
        log.warning("ticket.unknown_booking booking_id=%s", values["booking_id"])
        return False
    statement = insert(BookingTicket).values(**values)
    applied = db.execute(
        statement.on_conflict_do_update(
            index_elements=["booking_id"],
            set_={k: statement.excluded[k] for k in values if k != "booking_id"},
            where=BookingTicket.rendered_at < statement.excluded.rendered_at,
        ).returning(BookingTicket.booking_id)
    ).first()
    return applied is not None


# ---------------------------------------------------------------- check-in


@dataclass(frozen=True)
class CheckIn:
    booking: Booking
    checked_in_at: datetime


@lru_cache
def _verify_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"{path} is not an Ed25519 public key")
    return key


def verify_token(token: str, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """The token's claims, or a Problem: expired, or invalid (tampered, foreign, malformed)."""
    if not settings.ticket_verify_key_file.is_file():
        raise TicketsUnavailableError("Check-in is not configured (no ticket verification key)")
    try:
        return jwt.decode(
            token,
            _verify_key(settings.ticket_verify_key_file),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=settings.ticket_clock_leeway_s,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TicketExpiredError("This ticket has expired") from exc
    except jwt.InvalidTokenError as exc:
        log.warning("checkin.invalid_token reason=%s", exc)
        raise TicketInvalidError("This ticket is not valid: it was altered or not issued by Smart Tours") from exc


def check_in(db: Session, token: str, operator_id: str, settings: Settings, now: datetime | None = None) -> CheckIn:
    now = now or datetime.now(UTC)
    claims = verify_token(token, settings, now)
    try:
        booking_id = int(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise TicketInvalidError("This ticket is not valid") from exc
    try:
        # Locked: two scans of one ticket at the same moment are decided one after the other.
        booking = db.execute(
            select(Booking).where(Booking.id == booking_id).with_for_update(of=Booking)
        ).scalar_one_or_none()
        if booking is None:
            raise NotFoundError("No booking matches this ticket", booking_id=booking_id)
        departure = booking.availability
        if claims.get("ref") != booking.reference or claims.get("aid") != booking.availability_id:
            raise TicketInvalidError("This ticket does not match its booking", booking_id=booking_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise BookingNotConfirmedError(
                f"The booking is {booking.status}, not confirmed", booking_id=booking_id, booking_status=booking.status
            )
        zone = ZoneInfo(departure.time_zone)
        departure_day = departure.starts_at.astimezone(zone).date()
        today = now.astimezone(zone).date()
        if departure_day != today:
            raise WrongDayError(
                f"This ticket is for {departure_day.isoformat()}, not today "
                f"({today.isoformat()}, {departure.time_zone})",
                booking_id=booking_id,
                departure_date=departure_day.isoformat(),
                today=today.isoformat(),
            )
        if booking.checked_in_at is not None:
            raise AlreadyCheckedInError(
                "This ticket has already been used",
                booking_id=booking_id,
                checked_in_at=booking.checked_in_at.isoformat(),
            )
        booking.checked_in_at = now
        booking.checked_in_by = operator_id
        db.commit()
    except Exception:
        db.rollback()
        raise
    log.info("checkin.accepted booking_id=%s operator=%s", booking_id, operator_id)
    return CheckIn(booking, now)
