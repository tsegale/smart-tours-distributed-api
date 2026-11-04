"""Ticket key rotation (docs/runbooks/ticket-key-rotation.md): verify by kid from notification-service's JWKS.

The cache is driven by a fake clock and a fake JWKS endpoint, so TTL and refetch limits are tested without sleeping.
"""

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import get_settings
from app.errors import TicketInvalidError, TicketsUnavailableError
from app.services.ticket_keys import TicketKeySet
from app.services.tickets import verify_token
from tests.conftest import OPERATOR, TICKET_KID, TICKET_SIGNING_KEY, TicketJwks, jwk
from tests.test_tickets import confirmed, scan, token_for

SETTINGS = get_settings()
NEW_KEY = Ed25519PrivateKey.generate()
NEW_KID = "test-tickets-2"
BOOKING = {"id": 18, "reference": "ST-7K3Q9P2M", "availability_id": 7, "starts_at": "2030-12-01T04:00:00+00:00"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def endpoint() -> TicketJwks:
    return TicketJwks()


@pytest.fixture
def keys(endpoint, clock) -> TicketKeySet:
    return TicketKeySet(SETTINGS, transport=httpx.MockTransport(endpoint.handle), clock=clock)


def verifies(token: str, keys: TicketKeySet) -> bool:
    return verify_token(token, SETTINGS, keys=keys)["ref"] == BOOKING["reference"]


def test_during_the_overlap_tokens_from_the_previous_and_the_new_key_both_verify(endpoint, keys):
    endpoint.keys = [jwk(TICKET_SIGNING_KEY, TICKET_KID), jwk(NEW_KEY, NEW_KID)]

    assert verifies(token_for(BOOKING), keys)  # signed before the rotation
    assert verifies(token_for(BOOKING, key=NEW_KEY, kid=NEW_KID), keys)  # signed after it


def test_a_ticket_signed_with_the_previous_key_checks_in_during_the_overlap(client, departure, book, ticket_jwks):
    ticket_jwks.keys = [jwk(NEW_KEY, NEW_KID), jwk(TICKET_SIGNING_KEY, TICKET_KID)]
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking), OPERATOR)

    assert response.status_code == 200, response.text


def test_a_newly_rotated_key_is_picked_up_on_first_sight(endpoint, keys, clock):
    assert verifies(token_for(BOOKING), keys)
    endpoint.keys = [jwk(NEW_KEY, NEW_KID), jwk(TICKET_SIGNING_KEY, TICKET_KID)]  # rotated in since
    clock.now += SETTINGS.ticket_jwks_min_refetch_s  # well inside the TTL

    assert verifies(token_for(BOOKING, key=NEW_KEY, kid=NEW_KID), keys)  # unknown kid: refetched
    assert endpoint.requests == 2


def test_made_up_kids_cannot_make_every_scan_fetch(endpoint, keys, clock):
    assert verifies(token_for(BOOKING), keys)

    for n in range(5):
        with pytest.raises(TicketInvalidError):
            verify_token(token_for(BOOKING, kid=f"made-up-{n}"), SETTINGS, keys=keys)
    assert endpoint.requests == 1  # within the refetch interval of the first fetch: none of them fetched

    clock.now += SETTINGS.ticket_jwks_min_refetch_s
    for n in range(5):
        with pytest.raises(TicketInvalidError):
            verify_token(token_for(BOOKING, kid=f"made-up-again-{n}"), SETTINGS, keys=keys)
    assert endpoint.requests == 2  # one refetch for all five


def test_known_keys_are_cached_for_the_ttl_then_refetched(endpoint, keys, clock):
    assert verifies(token_for(BOOKING), keys)
    clock.now += SETTINGS.ticket_jwks_ttl_s - 1
    assert verifies(token_for(BOOKING), keys)
    assert endpoint.requests == 1

    clock.now += 1
    assert verifies(token_for(BOOKING), keys)
    assert endpoint.requests == 2


def test_once_the_previous_key_is_retired_its_tickets_stop_verifying(endpoint, keys, clock):
    endpoint.keys = [jwk(TICKET_SIGNING_KEY, TICKET_KID), jwk(NEW_KEY, NEW_KID)]
    assert verifies(token_for(BOOKING), keys)

    endpoint.keys = [jwk(NEW_KEY, NEW_KID)]  # end of the overlap window
    clock.now += SETTINGS.ticket_jwks_ttl_s

    with pytest.raises(TicketInvalidError):
        verify_token(token_for(BOOKING), SETTINGS, keys=keys)
    assert verifies(token_for(BOOKING, key=NEW_KEY, kid=NEW_KID), keys)


def test_known_keys_keep_working_while_the_jwks_is_down(endpoint, keys, clock):
    assert verifies(token_for(BOOKING), keys)
    endpoint.down = True
    clock.now += SETTINGS.ticket_jwks_ttl_s  # stale, and the refetch fails

    assert verifies(token_for(BOOKING), keys)
    assert endpoint.requests == 2


def test_an_unknown_kid_while_the_jwks_is_down_is_an_outage_not_an_invalid_ticket(endpoint, keys, clock):
    assert verifies(token_for(BOOKING), keys)
    endpoint.down = True
    clock.now += SETTINGS.ticket_jwks_min_refetch_s

    with pytest.raises(TicketsUnavailableError):
        verify_token(token_for(BOOKING, key=NEW_KEY, kid=NEW_KID), SETTINGS, keys=keys)


def test_no_keys_ever_fetched_is_a_503(client, departure, book, ticket_jwks):
    ticket_jwks.down = True
    booking = confirmed(client, departure, book)

    response = scan(client, token_for(booking), OPERATOR)

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


def test_keys_that_are_not_ed25519_signing_keys_are_ignored(endpoint, keys):
    rsa = {"kty": "RSA", "kid": "rsa-1", "n": "sXch", "e": "AQAB"}
    encryption = {**jwk(NEW_KEY, NEW_KID), "use": "enc"}
    endpoint.keys = [rsa, encryption, jwk(TICKET_SIGNING_KEY, TICKET_KID)]

    assert verifies(token_for(BOOKING), keys)
    with pytest.raises(TicketInvalidError):
        verify_token(token_for(BOOKING, key=NEW_KEY, kid=NEW_KID), SETTINGS, keys=keys)


def test_a_token_without_a_kid_is_invalid(keys):
    import jwt

    token = jwt.encode({"sub": "18"}, TICKET_SIGNING_KEY, algorithm="EdDSA")

    with pytest.raises(TicketInvalidError):
        verify_token(token, SETTINGS, keys=keys)
