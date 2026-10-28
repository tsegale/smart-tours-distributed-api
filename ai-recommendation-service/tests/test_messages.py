import json
import uuid
from contextlib import contextmanager

import pytest

from app import messages
from app.messages import PoisonMessageError, handle_tour_event, parse_event
from tests.fakes import FakeEmbedder

# Exactly what Jackson produces for com.smarttours.core.events.TourCreatedEvent.
JAVA_EVENT = json.dumps(
    {
        "eventId": "8a6f0f3e-5b8b-4b6e-9a51-2f0d8f2f6c11",
        "eventType": "tour.created",
        "occurredAt": "2026-10-27T10:15:30.123456Z",
        "tourId": 42,
        "name": "Sossusvlei Sunrise Dunes",
        "description": "Climb Dune 45 at dawn",
    }
).encode()


class FakeSession:
    @contextmanager
    def begin(self):
        yield


@contextmanager
def fake_session_factory():
    yield FakeSession()


def test_parses_the_java_event_contract():
    event = parse_event(JAVA_EVENT)
    assert event.tour_id == 42
    assert event.event_id == uuid.UUID("8a6f0f3e-5b8b-4b6e-9a51-2f0d8f2f6c11")
    assert event.description == "Climb Dune 45 at dawn"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"{}", json.dumps({"eventId": "x", "tourId": "abc"}).encode(), b'{"tourId": -1}'],
)
def test_malformed_events_are_poison(body):
    with pytest.raises(PoisonMessageError):
        parse_event(body)


def test_handler_embeds_title_and_description_and_upserts(monkeypatch):
    stored = {}
    monkeypatch.setattr(messages.repository, "upsert_embedding", lambda db, **kw: stored.update(kw))
    embedder = FakeEmbedder()

    handle_tour_event(JAVA_EVENT, fake_session_factory, embedder)

    assert embedder.calls == ["Sossusvlei Sunrise Dunes. Climb Dune 45 at dawn"]
    assert stored["tour_id"] == 42
    assert stored["model"] == "fake-embedder"
    assert len(stored["embedding"]) == 384
    assert stored["event_id"] == uuid.UUID("8a6f0f3e-5b8b-4b6e-9a51-2f0d8f2f6c11")
