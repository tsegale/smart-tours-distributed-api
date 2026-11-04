"""Check-in tokens and the PDF, with the real crypto and the real renderer (WeasyPrint)."""

import io
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pypdf import PdfReader

from app.config import get_settings
from app.emails import compose
from app.models import BookingSnapshot, NotificationType
from app.tickets import ALGORITHM, AUDIENCE, ISSUER, issue_token, render_pdf
from tests.conftest import SIGNING_KEY

PUBLIC_KEY = SIGNING_KEY.public_key()


def snapshot(starts_at: datetime, time_zone: str = "Africa/Windhoek", **overrides) -> BookingSnapshot:
    values = {
        "booking_id": 18,
        "reference": "ST-7K3Q9P2M",
        "user_id": "alice",
        "contact_email": "alice@example.com",
        "status": "CONFIRMED",
        "status_changed_at": datetime.now(UTC),
        "slots": 2,
        "total_amount": "3700.00",
        "currency": "NAD",
        "refund_amount": None,
        "availability_id": 7,
        "tour_id": 42,
        "tour_name": "Sossusvlei Sunrise Dunes & Deadvlei",
        "meeting_point": "Sesriem gate",
        "time_zone": time_zone,
        "starts_at": starts_at,
    }
    return BookingSnapshot(**{**values, **overrides})


def verify(token: str, **options) -> dict:
    return jwt.decode(token, PUBLIC_KEY, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER, **options)


def test_the_token_round_trips_with_the_public_key():
    booking = snapshot(datetime(2030, 12, 1, 4, 0, tzinfo=UTC))

    token = issue_token(booking, SIGNING_KEY, "tickets-1", timedelta(hours=24))
    claims = verify(token.value)

    assert claims["sub"] == "18"
    assert claims["ref"] == "ST-7K3Q9P2M"
    assert claims["aid"] == 7
    assert claims["dep"] == "2030-12-01T04:00:00+00:00"
    assert datetime.fromtimestamp(claims["exp"], UTC) == datetime(2030, 12, 2, 4, 0, tzinfo=UTC)
    assert jwt.get_unverified_header(token.value)["kid"] == "tickets-1"


def test_a_tampered_token_is_refused():
    token = issue_token(snapshot(datetime(2030, 12, 1, 4, 0, tzinfo=UTC)), SIGNING_KEY, "k", timedelta(hours=24))
    header, payload, signature = token.value.split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b'"sub":"18"', b'"sub":"19"')
    ).decode()

    with pytest.raises(jwt.InvalidSignatureError):
        verify(f"{header}.{forged_payload}.{signature}")


def test_a_token_signed_with_another_key_is_refused():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    other = issue_token(snapshot(datetime(2030, 12, 1, 4, 0, tzinfo=UTC)), Ed25519PrivateKey.generate(), "k",
                        timedelta(hours=24))  # fmt: skip
    with pytest.raises(jwt.InvalidSignatureError):
        verify(other.value)


def test_an_expired_token_is_refused():
    departed = snapshot(datetime(2026, 1, 10, 4, 0, tzinfo=UTC))
    token = issue_token(departed, SIGNING_KEY, "k", timedelta(hours=24), now=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(jwt.ExpiredSignatureError):
        verify(token.value)


def test_the_pdf_carries_the_reference_tour_and_local_departure():
    # 22:05 UTC on the 1st is 00:05 on the 2nd in Windhoek: the ticket shows the tour's local date.
    booking = snapshot(datetime(2030, 12, 1, 22, 5, tzinfo=UTC))
    token = issue_token(booking, SIGNING_KEY, "k", timedelta(hours=24))

    pdf = render_pdf(booking, token)

    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert pdf.startswith(b"%PDF-")
    assert "ST-7K3Q9P2M" in text
    assert "Sossusvlei Sunrise Dunes & Deadvlei" in text
    assert "Monday, 02 December 2030, 00:05" in text
    assert "Sesriem gate" in text


def test_the_qr_code_on_the_pdf_decodes_to_a_token_that_verifies():
    """The full round trip a guide's scanner makes: render, print, scan, verify."""
    import pypdfium2
    import zxingcpp

    booking = snapshot(datetime(2030, 12, 1, 4, 0, tzinfo=UTC))
    token = issue_token(booking, SIGNING_KEY, "tickets-1", timedelta(hours=24))
    pdf = render_pdf(booking, token)

    page = pypdfium2.PdfDocument(pdf)[0]
    [code] = [c for c in zxingcpp.read_barcodes(page.render(scale=200 / 72).to_pil())
              if c.format == zxingcpp.BarcodeFormat.QRCode]  # fmt: skip

    assert code.text == token.value
    assert verify(code.text)["ref"] == "ST-7K3Q9P2M"


@pytest.mark.parametrize(
    ("utc", "local"),
    [
        (datetime(2030, 12, 1, 22, 5, tzinfo=UTC), "Monday, 02 December 2030, 00:05"),
        (datetime(2030, 12, 1, 21, 55, tzinfo=UTC), "Sunday, 01 December 2030, 23:55"),
    ],
)
def test_the_reminder_shows_the_departure_in_the_tours_time_zone(utc, local):
    message = compose(get_settings(), NotificationType.REMINDER, snapshot(utc), "alice@example.com", "<r@x>")

    plain = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    assert local in plain and local in html
    assert "Africa/Windhoek, UTC+02:00" in plain
    assert "Sossusvlei Sunrise Dunes & Deadvlei" in plain  # plain text is not HTML-escaped
    assert "Sossusvlei Sunrise Dunes &amp; Deadvlei" in html  # HTML is
    assert message["Subject"] == "Tomorrow: Sossusvlei Sunrise Dunes & Deadvlei"
