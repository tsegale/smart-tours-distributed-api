"""Every pair of statuses, legal or not, against the one transition table."""

import itertools
import logging
from types import SimpleNamespace

import pytest

from app.domain.booking_status import TRANSITIONS, transition
from app.errors import IllegalTransitionError
from app.models import BookingStatus as S

LEGAL = {
    (S.PENDING_PAYMENT, S.CONFIRMED),
    (S.PENDING_PAYMENT, S.EXPIRED),
    (S.PENDING_PAYMENT, S.CANCELLED),
    (S.CONFIRMED, S.CANCELLED),
    (S.CONFIRMED, S.REFUNDED),
    (S.EXPIRED, S.CONFIRMED),  # late payment, seats still free
    (S.EXPIRED, S.REFUNDED),  # late payment, sold out
    (S.CANCELLED, S.REFUNDED),  # payment captured after a cancellation
}
ALL_PAIRS = list(itertools.product(S, S))


def booking(status: S):
    return SimpleNamespace(id=7, status=status, confirmed_at=None, cancelled_at=None)


def test_table_is_exactly_the_documented_one():
    assert {(a, b) for a, targets in TRANSITIONS.items() for b in targets} == LEGAL
    assert set(TRANSITIONS) == set(S)
    assert TRANSITIONS[S.REFUNDED] == frozenset()  # terminal


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_moves_apply(current, target, caplog):
    b = booking(current)
    with caplog.at_level(logging.INFO, logger="booking.transition"):
        transition(b, target, reason="test")
    assert b.status == target
    [record] = caplog.records
    assert record.getMessage() == f"booking.transition booking_id=7 from={current} to={target} reason=test"
    assert (record.booking_id, record.from_status, record.to_status, record.reason) == (7, current, target, "test")


@pytest.mark.parametrize(("current", "target"), sorted(set(ALL_PAIRS) - LEGAL))
def test_illegal_moves_raise_409_and_change_nothing(current, target):
    b = booking(current)
    with pytest.raises(IllegalTransitionError) as raised:
        transition(b, target, reason="test")
    assert raised.value.status == 409
    assert raised.value.extra == {"booking_id": 7, "current_status": current, "requested_status": target}
    assert b.status == current


def test_timestamps_follow_the_transition():
    b = booking(S.PENDING_PAYMENT)
    transition(b, S.CONFIRMED, reason="paid")
    assert b.confirmed_at is not None and b.cancelled_at is None
    transition(b, S.REFUNDED, reason="cancelled early")
    assert b.cancelled_at is not None


def test_illegal_transition_is_a_problem_response(client, departure, book):
    """An EXPIRED booking can't be cancelled: the table refuses, rendered as RFC 9457."""
    from sqlalchemy import text

    from app.database import engine
    from tests.conftest import ALICE

    booking_id = book(departure())["id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE bookings SET status = 'EXPIRED' WHERE id = :id"), {"id": booking_id})

    response = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("/illegal-transition")
    assert response.json()["current_status"] == "EXPIRED"
