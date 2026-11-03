"""Email: composing multipart HTML + text messages, and sending them through an EmailSender.

SmtpEmailSender is the only implementation (Mailpit in dev, any SMTP relay in production). An SES or SendGrid
adapter implements the same Protocol and is picked by configuration; nothing else changes.

Failures are classified for the retry policy: TransientEmailError (connection problems, 4xx replies) is retried
with backoff; PermanentEmailError (5xx replies, a refused recipient) is not, and the notification fails at once.
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from typing import Protocol

from .config import Settings
from .models import BookingSnapshot, NotificationType
from .tickets import TEMPLATES, local_departure

SUBJECTS: dict[NotificationType, str] = {
    NotificationType.CONFIRMATION: "Your booking {reference} is confirmed: {tour}",
    NotificationType.CANCELLATION: "Your booking {reference} is cancelled",
    NotificationType.REFUND: "Your booking {reference} is cancelled and refunded",
    NotificationType.HOLD_EXPIRED: "Your seats for {tour} were released",
    NotificationType.REMINDER: "Tomorrow: {tour}",
}


class TransientEmailError(Exception):
    """Worth retrying: the server or the connection may be fine again soon."""


class PermanentEmailError(Exception):
    """Retrying won't help: the server refused this message or recipient."""


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None:
        """Deliver to the next hop, or raise TransientEmailError / PermanentEmailError."""
        ...


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, message: EmailMessage) -> None:
        s = self._settings
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            if all(400 <= code < 500 for code in codes):
                raise TransientEmailError(f"recipient deferred: {exc.recipients}") from exc
            raise PermanentEmailError(f"recipient refused: {exc.recipients}") from exc
        except smtplib.SMTPResponseException as exc:
            if 400 <= exc.smtp_code < 500:
                raise TransientEmailError(f"SMTP {exc.smtp_code}: {exc.smtp_error!r}") from exc
            raise PermanentEmailError(f"SMTP {exc.smtp_code}: {exc.smtp_error!r}") from exc
        except OSError as exc:  # disconnects, refused connections, timeouts: smtplib's errors are OSErrors too
            raise TransientEmailError(f"{type(exc).__name__}: {exc}") from exc


def compose(
    settings: Settings,
    notification_type: NotificationType,
    booking: BookingSnapshot,
    recipient: str,
    message_id: str,
    ticket_pdf: bytes | None = None,
) -> EmailMessage:
    """Plain text first, HTML as the alternative, the ticket attached for confirmations."""
    departure = local_departure(booking)
    context = {
        "booking": booking,
        "departure": departure,
        "offset": departure.strftime("%z")[:3] + ":" + departure.strftime("%z")[3:],
        "type": notification_type,
    }
    name = notification_type.lower()
    message = EmailMessage()
    message["Subject"] = SUBJECTS[notification_type].format(reference=booking.reference, tour=booking.tour_name)
    message["From"] = settings.mail_from
    message["To"] = recipient
    message["Message-ID"] = message_id
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["X-Booking-Reference"] = booking.reference
    message.set_content(TEMPLATES.get_template(f"email/{name}.txt.j2").render(**context))
    message.add_alternative(TEMPLATES.get_template(f"email/{name}.html.j2").render(**context), subtype="html")
    if ticket_pdf is not None:
        message.add_attachment(
            ticket_pdf, maintype="application", subtype="pdf", filename=f"ticket-{booking.reference}.pdf"
        )
    return message
