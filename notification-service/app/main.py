"""HTTP side of notification-service: health, metrics and two admin endpoints. The work happens in the bridge and
the Celery workers; this process only reads, and re-queues a failed notification on an operator's request.

Not routed by the gateway: reachable on the internal network only, identity from X-User-* headers like every
service behind the gateway (ADR 0004).
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from . import tickets
from .config import get_settings
from .database import SessionLocal, engine
from .dispatch import dispatch
from .errors import register_error_handlers
from .logs import configure_logging
from .models import Notification, NotificationStatus

configure_logging()
log = logging.getLogger(__name__)


class NotificationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    type: str
    status: str
    recipient: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


def require_operator(x_user_roles: Annotated[str | None, Header()] = None) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",")}
    if not roles & {"OPERATOR", "ADMIN"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires one of the roles: OPERATOR, ADMIN")


class NotificationCollector(Collector):
    """Read at scrape time: notifications by status and type, and the ticket.rendered outbox lag."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        by_status = GaugeMetricFamily("notifications", "Notifications by status and type", labels=["status", "type"])
        lag = GaugeMetricFamily("outbox_oldest_unpublished_age_seconds", "Age of the oldest unpublished event")
        pending = GaugeMetricFamily("outbox_unpublished_events", "Events written but not yet confirmed")
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    select(Notification.status, Notification.type, func.count()).group_by(
                        Notification.status, Notification.type
                    )
                ).all()
                age, backlog = conn.execute(
                    text(
                        "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(occurred_at)), 0), count(*) "
                        "FROM outbox WHERE published_at IS NULL"
                    )
                ).one()
        except SQLAlchemyError:
            log.warning("notification metrics unavailable", exc_info=True)
            return
        for state, kind, count in rows:
            by_status.add_metric([state, kind], count)
        lag.add_metric([], float(age))
        pending.add_metric([], float(backlog))
        yield by_status
        yield lag
        yield pending


registry = CollectorRegistry()
registry.register(NotificationCollector())

app = FastAPI(
    title="Smart Tours - Notification Service",
    version="1.0.0",
    description="Booking emails and PDF tickets. Health and admin endpoints; the work runs in Celery workers.",
)
register_error_handlers(app)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "UP"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.get("/.well-known/jwks.json", tags=["tickets"])
def ticket_jwks() -> Response:
    """The public keys that verify check-in tokens (RFC 7517), chosen by the token's kid. Public by nature, but
    served on the internal network only: booking-service is the one verifier."""
    settings = get_settings()
    try:
        keys = tickets.published_keys(settings.ticket_public_keys_dir)
    except (OSError, ValueError) as exc:
        log.error("jwks.unreadable dir=%s error=%s", settings.ticket_public_keys_dir, exc)
        keys = []
    if not keys:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No ticket verification keys are published")
    return Response(
        content=json.dumps({"keys": keys}),
        media_type="application/jwk-set+json",
        headers={"Cache-Control": f"public, max-age={settings.jwks_max_age_s}"},
    )


@app.get("/admin/notifications", response_model=list[NotificationView], dependencies=[Depends(require_operator)])
def list_notifications(booking_id: Annotated[int, Query(gt=0)]) -> list[NotificationView]:
    with SessionLocal() as db:
        rows = db.execute(select(Notification).where(Notification.booking_id == booking_id).order_by(Notification.id))
        return [NotificationView.model_validate(n) for n in rows.scalars()]


@app.post(
    "/admin/notifications/{notification_id}/retry",
    response_model=NotificationView,
    dependencies=[Depends(require_operator)],
)
def retry_notification(notification_id: int) -> NotificationView:
    """Put a FAILED notification back to PENDING and dispatch it again (after fixing what made it fail)."""
    with SessionLocal() as db:
        notification = db.get(Notification, notification_id, with_for_update=True)
        if notification is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Notification {notification_id} does not exist")
        if notification.status != NotificationStatus.FAILED:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Only FAILED notifications can be retried, not {notification.status}"
            )
        notification.status = NotificationStatus.PENDING
        notification.last_error = None
        db.commit()
        view = NotificationView.model_validate(notification)
    dispatch(SessionLocal, [notification_id])
    log.info("notification.retried_by_operator notification_id=%s", notification_id,
             extra={"notification_id": notification_id})  # fmt: skip
    return view
