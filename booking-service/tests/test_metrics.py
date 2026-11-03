"""/metrics exports the outbox relay lag, read from the table at scrape time."""

import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, update

from app.database import SessionLocal
from app.models import OutboxEvent


def gauge(client, name: str) -> float:
    body = client.get("/metrics").text
    match = re.search(rf"^{name} (\S+)$", body, re.MULTILINE)
    assert match, f"{name} missing from /metrics"
    return float(match.group(1))


@pytest.mark.synthetic_events
def test_lag_is_the_age_of_the_oldest_unpublished_event(client):
    assert gauge(client, "outbox_oldest_unpublished_age_seconds") == 0
    assert gauge(client, "outbox_unpublished_events") == 0
    event_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(
            OutboxEvent(
                id=event_id,
                aggregate_type="test",
                aggregate_id="1",
                event_type="test.happened",
                payload={"eventId": str(event_id)},
            )
        )
        db.flush()
        # The database's clock, not this process's: the gauge measures against now() in Postgres, and the
        # Docker VM's clock can drift from the host's.
        db.execute(update(OutboxEvent).values(occurred_at=func.now() - timedelta(seconds=90)))
        db.commit()

    assert gauge(client, "outbox_oldest_unpublished_age_seconds") >= 90
    assert gauge(client, "outbox_unpublished_events") == 1

    with SessionLocal() as db:
        db.execute(update(OutboxEvent).values(published_at=func.now()))
        db.commit()
    assert gauge(client, "outbox_oldest_unpublished_age_seconds") == 0
    assert gauge(client, "outbox_unpublished_events") == 0
