"""Ticket download and QR check-in (ADR 0015), and the ticket.rendered listener that makes downloads possible."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.database import SessionLocal
from app.jobs.ticket_listener import Action, MalformedEventError, decide, handle
from app.models import Booking, BookingTicket
from tests.conftest import ALICE, BOB, OPERATOR, TICKET_SIGNING_KEY
from tests.stripe_events import checkout_event, deliver
from tests.test_webhooks import COMPLETED

WINDHOEK = ZoneInfo("Africa/Windhoek")


def today_at_noon() -> str:
    """A departure today in Windhoek, whatever the time of day the suite runs."""
    return datetime.now(WINDHOEK).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()


def confirmed(client, departure, book, starts_at: str | None = None) -> dict:
    booking = book(departure(starts_at=starts_at or today_at_noon()), slots=2)
    assert deliver(client, checkout_event(COMPLETED, booking["id"])).status_code == 200
    return client.get(f"/api/bookings/{booking['id']}", headers=ALICE).json()


def token_for(booking: dict, *, key: Ed25519PrivateKey = TICKET_SIGNING_KEY, expires_in: timedelta = timedelta(days=1),
              **overrides) -> str:  # fmt: skip
    now = datetime.now(UTC)
    claims = {
        "iss": "smarttours-notifications",
        "aud": "smarttours-checkin",
        "sub": str(booking["id"]),
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "ref": booking["reference"],
        "aid": booking["availability_id"],
        "dep": booking["starts_at"],
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "tickets-1", "typ": "checkin+jwt"})


def scan(client, token: str, headers=OPERATOR):
    return client.post("/api/check-ins", json={"token": token}, headers=headers)


def problem_type(response) -> str:
    assert response.headers["content-type"] == "application/problem+json", response.text
    return response.json()["type"].rsplit("/", 1)[-1]


# ---------------------------------------------------------------- check-in


def test_a_valid_ticket_checks_in_once(client, departure, book):
    booking = confirmed(client, departure, book)

    first = scan(client, token_for(booking))
    second = scan(client, token_for(booking))

    assert first.status_code == 200, first.text
    assert first.json()["reference"] == booking["reference"]
    assert first.json()["slots"] == 2
    assert first.json()["time_zone"] == "Africa/Windhoek"
    assert second.status_code == 409
    assert problem_type(second) == "ticket-already-used"
    assert second.json()["checked_in_at"] == first.json()["checked_in_at"].replace("Z", "+00:00")
    with SessionLocal() as db:
        stored = db.get(Booking, booking["id"])
        assert stored.checked_in_by == "operator-1"


def test_a_tampered_ticket_is_refused(client, departure, book):
    booking = confirmed(client, departure, book)
    header, payload, signature = token_for(booking).split(".")
    claims = json.loads(jwt.utils.base64url_decode(payload))
    forged = jwt.utils.base64url_encode(json.dumps({**claims, "sub": "999"}).encode()).decode()

    response = scan(client, f"{header}.{forged}.{signature}")

    assert response.status_code == 422
    assert problem_type(response) == "ticket-invalid"


def test_a_ticket_signed_with_another_key_is_refused(client, departure, book):
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking, key=Ed25519PrivateKey.generate()))

    assert response.status_code == 422
    assert problem_type(response) == "ticket-invalid"


def test_a_ticket_for_another_booking_reference_is_refused(client, departure, book):
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking, ref="ST-00000000"))

    assert response.status_code == 422
    assert problem_type(response) == "ticket-invalid"


def test_an_expired_ticket_is_refused(client, departure, book):
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking, expires_in=timedelta(hours=-2)))

    assert response.status_code == 410
    assert problem_type(response) == "ticket-expired"


def test_a_ticket_for_another_day_is_refused(client, departure, book):
    tomorrow = (datetime.now(WINDHOEK) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    booking = confirmed(client, departure, book, starts_at=tomorrow.isoformat())

    response = scan(client, token_for(booking))

    assert response.status_code == 409
    assert problem_type(response) == "ticket-wrong-day"
    assert response.json()["departure_date"] == tomorrow.date().isoformat()


def test_a_cancelled_booking_cannot_check_in(client, departure, book):
    booking = confirmed(client, departure, book)
    assert client.post(f"/api/bookings/{booking['id']}/cancel", headers=ALICE).status_code == 200

    response = scan(client, token_for(booking))

    assert response.status_code == 409
    assert problem_type(response) == "booking-not-confirmed"


def test_only_operators_check_in(client, departure, book):
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking), headers=ALICE)

    assert response.status_code == 403


# ---------------------------------------------------------------- tickets


def ticket_rendered(booking_id: int, occurred_at: datetime | None = None, key: str | None = None) -> bytes:
    return json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": "ticket.rendered",
            "occurredAt": (occurred_at or datetime.now(UTC)).isoformat(),
            "schemaVersion": 1,
            "bookingId": booking_id,
            "reference": "ST-00000001",
            "bucket": "tickets",
            "objectKey": key or f"tickets/{booking_id}/ST-00000001.pdf",
            "contentType": "application/pdf",
            "sizeBytes": 48213,
            "sha256": "a" * 64,
        }
    ).encode()


def test_no_ticket_yet_is_a_404_problem(client, departure, book):
    booking = confirmed(client, departure, book)

    response = client.get(f"/api/bookings/{booking['id']}/ticket", headers=ALICE, follow_redirects=False)

    assert response.status_code == 404
    assert problem_type(response) == "ticket-not-ready"


def test_the_owner_is_redirected_to_a_short_lived_presigned_url(client, departure, book):
    booking = confirmed(client, departure, book)
    handle(ticket_rendered(booking["id"]))

    response = client.get(f"/api/bookings/{booking['id']}/ticket", headers=ALICE, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert (url.scheme, url.netloc, url.path) == (
        "http",
        "localhost:9000",
        f"/tickets/tickets/{booking['id']}/ST-00000001.pdf",
    )
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-Credential"][0].startswith("tickets-reader/")
    assert "X-Amz-Signature" in query


def test_someone_elses_ticket_is_404(client, departure, book):
    booking = confirmed(client, departure, book)
    handle(ticket_rendered(booking["id"]))

    response = client.get(f"/api/bookings/{booking['id']}/ticket", headers=BOB, follow_redirects=False)

    assert response.status_code == 404


def test_the_listener_keeps_the_newest_ticket_and_ignores_unknown_bookings(client, departure, book):
    booking = confirmed(client, departure, book)
    now = datetime.now(UTC)

    assert handle(ticket_rendered(booking["id"], now, key="tickets/new.pdf")) is True
    assert handle(ticket_rendered(booking["id"], now - timedelta(minutes=5), key="tickets/old.pdf")) is False
    assert handle(ticket_rendered(987654)) is False

    with SessionLocal() as db:
        keys = list(db.execute(select(BookingTicket.object_key)).scalars())
    assert keys == ["tickets/new.pdf"]


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        json.dumps({"eventType": "booking.confirmed"}).encode(),
        json.dumps({"eventType": "ticket.rendered"}).encode(),
    ],
)
def test_a_malformed_ticket_event_is_dead_lettered(raw):
    with pytest.raises(MalformedEventError):
        handle(raw)
    assert decide(raw, None, max_retries=5) is Action.DEAD_LETTER
