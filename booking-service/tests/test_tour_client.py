"""The cross-service call to tour-core-service, with tour-core mocked by respx."""

from decimal import Decimal

import httpx
import respx

from tests.conftest import ALICE, OPERATOR, TOUR


def create_departure(client, tour_id: int = 1, starts_at: str = "2030-12-01T06:00:00Z"):
    return client.post(
        "/api/availability",
        json={"tour_id": tour_id, "starts_at": starts_at, "total_slots": 10},
        headers=OPERATOR,
    )


def test_unknown_tour_is_404_problem(client, tour_core):
    route = tour_core["get_tour"].mock(return_value=httpx.Response(404, json={"title": "Not Found", "status": 404}))

    response = create_departure(client, tour_id=42)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("/tour-not-found")
    assert response.json()["tour_id"] == 42
    assert route.call_count == 1  # a 404 is an answer: not retried


def test_timeouts_are_retried_then_503(client, tour_core):
    route = tour_core["get_tour"].mock(side_effect=httpx.ReadTimeout("slow"))

    response = create_departure(client)

    assert response.status_code == 503
    assert response.json()["type"].endswith("/upstream-unavailable")
    assert response.json()["retryable"] is True
    assert route.call_count == 3  # first try + TOUR_SERVICE_MAX_RETRIES (2)


def test_5xx_then_success_is_retried(client, tour_core):
    route = tour_core["get_tour"].mock(
        side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200, json=TOUR)]
    )

    response = create_departure(client)

    assert response.status_code == 201, response.text
    assert route.call_count == 3
    assert Decimal(response.json()["unit_price"]) == Decimal("1850.00")


def test_connection_refused_then_success(client, tour_core):
    route = tour_core["get_tour"].mock(side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=TOUR)])

    assert create_departure(client).status_code == 201
    assert route.call_count == 2


def test_client_errors_other_than_404_are_not_retried(client, tour_core):
    route = tour_core["get_tour"].mock(return_value=httpx.Response(400))

    assert create_departure(client).status_code == 503
    assert route.call_count == 1


def test_price_is_parsed_exactly(client, tour_core):
    # 0.1 + 0.2 style prices must not pass through a float.
    tour_core["get_tour"].mock(
        return_value=httpx.Response(
            200,
            content=b'{"id":1,"name":"Etosha","price":1234567.89,"currency":"NAD"}',
            headers={"content-type": "application/json"},
        )
    )

    response = create_departure(client)

    assert response.json()["unit_price"] == "1234567.89"


def test_booking_price_never_changes_when_the_tour_price_does(client, tour_core, book):
    first = create_departure(client, starts_at="2030-12-01T06:00:00Z").json()["id"]
    booking = book(first, slots=2)

    tour_core["get_tour"].mock(return_value=httpx.Response(200, json={**TOUR, "price": 2100.00}))
    second = create_departure(client, starts_at="2030-12-02T06:00:00Z").json()

    assert Decimal(second["unit_price"]) == Decimal("2100.00")  # new departures take the new price
    again = client.get(f"/api/bookings/{booking['id']}", headers=ALICE).json()
    assert (Decimal(again["unit_price"]), Decimal(again["total_amount"])) == (Decimal("1850.00"), Decimal("3700.00"))


def test_calls_go_to_the_configured_internal_url(client, tour_core):
    create_departure(client)
    request: httpx.Request = tour_core["get_tour"].calls.last.request
    assert str(request.url) == "http://tour-core.test/api/tours/1"
    assert isinstance(tour_core, respx.MockRouter)
