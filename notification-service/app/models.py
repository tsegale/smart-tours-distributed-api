"""notification-service's own database (ADR 0001): what it has heard, what it owes, and what it has sent."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class NotificationType(enum.StrEnum):
    CONFIRMATION = "CONFIRMATION"  # with the PDF ticket
    CANCELLATION = "CANCELLATION"
    REFUND = "REFUND"
    HOLD_EXPIRED = "HOLD_EXPIRED"
    REMINDER = "REMINDER"  # the day before departure, from Celery Beat


class NotificationStatus(enum.StrEnum):
    PENDING = "PENDING"  # owed; a task is (or will be) on its way
    SENT = "SENT"
    SKIPPED = "SKIPPED"  # no longer true when its turn came (a confirmation for a booking cancelled since)
    FAILED = "FAILED"  # permanent SMTP error or retries exhausted; the task is in the dead-letter queue


class ProcessedEvent(Base):
    """Event ids already applied. Inserted in the same transaction as the effect: duplicates change nothing."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookingSnapshot(Base):
    """The latest state of a booking as booking-service's lifecycle events told it: everything a notification
    or a reminder needs, so nothing here calls back. Updated only by a newer event (status_changed_at)."""

    __tablename__ = "booking_snapshots"

    booking_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reference: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slots: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[str] = mapped_column(String(20))  # exact decimal as published, never a float
    currency: Mapped[str] = mapped_column(String(3))
    refund_amount: Mapped[str | None] = mapped_column(String(20))
    availability_id: Mapped[int] = mapped_column(BigInteger)
    tour_id: Mapped[int] = mapped_column(BigInteger)
    tour_name: Mapped[str] = mapped_column(String(255))
    meeting_point: Mapped[str | None] = mapped_column(String(500))
    time_zone: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # The reminder query: confirmed bookings departing in a time window.
        Index("ix_booking_snapshots_reminders", "starts_at", postgresql_where=text("status = 'CONFIRMED'")),
    )


class Notification(Base):
    """One message owed to one booking. (booking_id, type) is unique: a duplicate event, a second Beat instance
    or an overlapping reminder window can't create a second one, so it can't be sent twice."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("booking_id", "type", name="uq_notifications_booking_type"),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'SKIPPED', 'FAILED')",
            name="ck_notifications_status",
        ),
        # The re-dispatch query: PENDING for too long.
        Index("ix_notifications_pending", "created_at", postgresql_where=text("status = 'PENDING'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=NotificationStatus.PENDING)
    recipient: Mapped[str] = mapped_column(String(320))
    # Deterministic, so a retried send carries the same id and a duplicate is recognisable downstream.
    message_id: Mapped[str] = mapped_column(String(255))
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # the event that created it, for logs
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    """The rendered PDF of a confirmed booking, in object storage."""

    __tablename__ = "tickets"

    booking_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reference: Mapped[str] = mapped_column(String(16))
    bucket: Mapped[str] = mapped_column(String(63))
    object_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The signing key's kid: a retired key stays published until max(token_expires_at) of its tickets has passed.
    # NULL for tickets rendered before keys had ids (the first key).
    key_id: Mapped[str | None] = mapped_column(String(64))
    rendered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    """ticket.rendered, written in the transaction that records the ticket (same table as booking-service's)."""

    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_unpublished", "occurred_at", "id", postgresql_where=text("published_at IS NULL")),
        Index("ix_outbox_published_at", "published_at", postgresql_where=text("published_at IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # W3C trace context of the transaction that wrote this row; the relay publishes inside it (ADR 0017).
    traceparent: Mapped[str | None] = mapped_column(String(55))
