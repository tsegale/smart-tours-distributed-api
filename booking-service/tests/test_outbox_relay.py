"""The outbox relay: at-least-once, no batch published twice by concurrent relays, nothing lost."""

import json
import os
import threading
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import OutboxEvent
from app.services import outbox
from app.services.outbox_relay import OutboundMessage, prune_published, relay_batch


class RecordingPublisher:
    """Stands in for RabbitMQ. Optionally blocks mid-publish, or fails after publishing."""

    def __init__(self, hold: threading.Event | None = None, fail_after_publish: bool = False) -> None:
        self.published: list[uuid.UUID] = []
        self.entered = threading.Event()
        self._hold = hold
        self._fail_after_publish = fail_after_publish
        self._lock = threading.Lock()

    def publish(self, messages: Sequence[OutboundMessage]) -> None:
        self.entered.set()
        if self._hold is not None:
            assert self._hold.wait(10), "test never released the publisher"
        with self._lock:
            self.published.extend(m.event_id for m in messages)
        if self._fail_after_publish:
            # The broker has the messages, but the relay dies before marking them.
            raise RuntimeError("simulated crash between publish and mark")


class BrokerDown:
    def publish(self, messages: Sequence[OutboundMessage]) -> None:
        raise ConnectionError("broker unreachable")


def write_events(count: int, start: datetime | None = None) -> list[uuid.UUID]:
    start = start or datetime.now(UTC)
    ids = []
    with SessionLocal() as db:
        for i in range(count):
            event_id = outbox.record(
                db, aggregate_type="test", aggregate_id=i, event_type="test.happened", data={"n": i}
            )
            ids.append(event_id)
        db.flush()
        # Distinct, increasing timestamps, so the expected publish order is unambiguous.
        for i, event_id in enumerate(ids):
            db.execute(
                update(OutboxEvent).where(OutboxEvent.id == event_id).values(occurred_at=start + timedelta(seconds=i))
            )
        db.commit()
    return ids


def unpublished() -> int:
    with SessionLocal() as db:
        return db.execute(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
        ).scalar_one()


def test_publishes_oldest_first_marks_them_and_then_is_idle():
    ids = write_events(5)
    publisher = RecordingPublisher()

    assert relay_batch(SessionLocal, publisher, batch_size=3) == 3
    assert relay_batch(SessionLocal, publisher, batch_size=3) == 2
    assert relay_batch(SessionLocal, publisher, batch_size=3) == 0

    assert publisher.published == ids
    assert unpublished() == 0


def test_payload_and_routing_key_reach_the_publisher(departure):
    availability_id = departure(total_slots=4)
    captured: list[OutboundMessage] = []

    class Capture:
        def publish(self, messages: Sequence[OutboundMessage]) -> None:
            captured.extend(messages)

    relay_batch(SessionLocal, Capture(), batch_size=10)

    [message] = captured
    assert message.routing_key == "availability.changed"
    assert message.payload["availabilityId"] == availability_id
    assert message.payload["eventId"] == str(message.event_id)
    assert json.loads(json.dumps(message.payload)) == message.payload  # serialisable as is


def test_two_relays_never_publish_the_same_batch():
    ids = write_events(20)
    release = threading.Event()
    first = RecordingPublisher(hold=release)
    second = RecordingPublisher()

    # The first relay claims rows 0-9 and stalls inside publish, still holding their row locks.
    t1 = threading.Thread(target=relay_batch, args=(SessionLocal, first, 10))
    t1.start()
    assert first.entered.wait(10)

    # The second relay runs meanwhile: SKIP LOCKED hands it the next ten, never the claimed ones.
    assert relay_batch(SessionLocal, second, 10) == 10
    release.set()
    t1.join(10)

    assert set(first.published).isdisjoint(second.published)
    assert first.published == ids[:10]
    assert second.published == ids[10:]
    assert unpublished() == 0


def test_many_relays_racing_publish_every_event_exactly_once():
    ids = write_events(200)
    publishers = [RecordingPublisher() for _ in range(4)]
    barrier = threading.Barrier(len(publishers))

    def drain(publisher: RecordingPublisher) -> None:
        barrier.wait()
        while relay_batch(SessionLocal, publisher, 7):
            pass

    threads = [threading.Thread(target=drain, args=(p,)) for p in publishers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    counts = Counter(event_id for p in publishers for event_id in p.published)
    assert set(counts) == set(ids)
    assert max(counts.values()) == 1
    assert unpublished() == 0


def test_crash_after_publish_before_mark_causes_a_duplicate_never_a_loss():
    ids = write_events(3)
    crashing = RecordingPublisher(fail_after_publish=True)

    with pytest.raises(RuntimeError, match="simulated crash"):
        relay_batch(SessionLocal, crashing, 10)
    assert unpublished() == 3, "the claim rolled back: nothing is marked"

    recovered = RecordingPublisher()
    assert relay_batch(SessionLocal, recovered, 10) == 3

    assert crashing.published == ids and recovered.published == ids  # each event went out twice
    assert unpublished() == 0


def test_broker_down_marks_nothing():
    write_events(2)

    with pytest.raises(ConnectionError):
        relay_batch(SessionLocal, BrokerDown(), 10)

    assert unpublished() == 2


def test_prune_deletes_only_rows_published_before_the_retention_window():
    now = datetime.now(UTC)
    old, recent, pending = write_events(3)
    with SessionLocal() as db:
        db.execute(update(OutboxEvent).where(OutboxEvent.id == old).values(published_at=now - timedelta(days=8)))
        db.execute(update(OutboxEvent).where(OutboxEvent.id == recent).values(published_at=now - timedelta(days=1)))
        db.commit()

    assert prune_published(SessionLocal, timedelta(days=7), batch_size=1, now=now) == 1

    with SessionLocal() as db:
        remaining = set(db.execute(select(OutboxEvent.id)).scalars())
    assert remaining == {recent, pending}


RABBITMQ_URL = os.environ.get("TEST_RABBITMQ_URL")


@pytest.mark.skipif(not RABBITMQ_URL, reason="TEST_RABBITMQ_URL not set; needs a real RabbitMQ")
def test_rabbit_publisher_delivers_confirmed_persistent_messages(departure):
    import pika

    from app.messaging import RabbitPublisher

    exchange = f"test.events.{uuid.uuid4().hex[:8]}"
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    channel = connection.channel()
    channel.exchange_declare(exchange, exchange_type="topic", durable=True)  # as the relay declares it
    queue = channel.queue_declare("", exclusive=True).method.queue
    channel.queue_bind(queue, exchange, routing_key="availability.*")

    availability_id = departure(total_slots=3)
    publisher = RabbitPublisher(RABBITMQ_URL, exchange)
    try:
        assert relay_batch(SessionLocal, publisher, 10) == 1
    finally:
        publisher.close()

    method, properties, body = channel.basic_get(queue, auto_ack=True)
    channel.exchange_delete(exchange)
    connection.close()
    assert method is not None, "nothing was routed to the bound queue"
    event = json.loads(body)
    assert event["availabilityId"] == availability_id
    assert properties.message_id == event["eventId"]
    assert properties.content_type == "application/json"
    assert properties.delivery_mode == 2
    assert unpublished() == 0
