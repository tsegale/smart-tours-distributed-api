from types import SimpleNamespace
from unittest.mock import MagicMock

import pika
import pytest

from app import worker
from app.config import Settings
from app.messages import PoisonMessageError
from app.worker import RETRY_HEADER, Action, apply, decide

SETTINGS = Settings(max_retries=3)


def ok(_body):
    return None


def transient_failure(_body):
    raise ConnectionError("database briefly unavailable")


def poison(_body):
    raise PoisonMessageError("bad payload")


class FakeChannel:
    def __init__(self):
        self.acked, self.rejected, self.published = [], [], []

    def basic_ack(self, delivery_tag):
        self.acked.append(delivery_tag)

    def basic_reject(self, delivery_tag, requeue):
        self.rejected.append((delivery_tag, requeue))

    def basic_publish(self, exchange, routing_key, body, properties):
        self.published.append(
            SimpleNamespace(exchange=exchange, routing_key=routing_key, body=body, properties=properties)
        )


def test_success_is_acked():
    assert decide(b"{}", None, ok, max_retries=3) is Action.ACK


def test_transient_failure_is_retried_until_the_limit():
    assert decide(b"{}", None, transient_failure, max_retries=3) is Action.RETRY
    assert decide(b"{}", {RETRY_HEADER: 2}, transient_failure, max_retries=3) is Action.RETRY
    assert decide(b"{}", {RETRY_HEADER: 3}, transient_failure, max_retries=3) is Action.DEAD_LETTER


def test_poison_message_goes_straight_to_the_dlq():
    """The original consumer requeued everything forever, so one bad message blocked the queue."""
    assert decide(b"garbage", None, poison, max_retries=3) is Action.DEAD_LETTER


def test_retry_republishes_with_incremented_count_then_acks():
    channel = FakeChannel()
    props = pika.BasicProperties(content_type="application/json", headers={RETRY_HEADER: 1, "trace": "abc"})

    apply(Action.RETRY, channel, SimpleNamespace(delivery_tag=7), props, b"payload", SETTINGS)

    [retry] = channel.published
    assert retry.exchange == ""
    assert retry.routing_key == "ai.tour-embedding.retry"
    assert retry.properties.headers == {RETRY_HEADER: 2, "trace": "abc"}
    assert retry.properties.delivery_mode == pika.DeliveryMode.Persistent.value
    assert channel.acked == [7]
    assert channel.rejected == []


def test_dead_letter_rejects_without_requeue():
    channel = FakeChannel()
    apply(Action.DEAD_LETTER, channel, SimpleNamespace(delivery_tag=9), pika.BasicProperties(), b"x", SETTINGS)
    assert channel.rejected == [(9, False)]
    assert channel.acked == []


def test_the_queues_are_declared_before_the_model_loads(monkeypatch):
    """A first start spends minutes downloading the model, and events published with no queue are lost."""
    order = []

    class Registry:
        def writers(self):
            order.append("model")
            raise SystemExit

    monkeypatch.setattr(worker, "configure_logging", lambda name: None)
    monkeypatch.setattr(worker, "configure_telemetry", lambda name: None)
    monkeypatch.setattr(worker, "declare_before_loading", lambda settings: order.append("queues"))
    monkeypatch.setattr(worker, "get_registry", Registry)
    monkeypatch.setattr(worker, "ensure_active", lambda db, model: None)
    monkeypatch.setattr(worker, "SessionLocal", MagicMock())

    with pytest.raises(SystemExit):
        worker.run()

    assert order == ["queues", "model"]


def test_a_broker_that_is_down_does_not_stop_the_worker_starting(monkeypatch):
    def refuse(_params):
        raise worker.AMQPConnectionError

    monkeypatch.setattr(worker.pika, "BlockingConnection", refuse)
    worker.declare_before_loading(worker.get_settings())
