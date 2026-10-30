# Smart Tours Distributed API

A polyglot microservices backend for discovering and booking tours in southern Africa. It covers
cache-backed geospatial and full-text search, overbooking-safe seat inventory, a transactional outbox with
an event-fed read model, rate limiting at the edge, and semantic recommendations.

![CI](https://github.com/tsegale/smart-tours-distributed-api/actions/workflows/ci.yml/badge.svg)

## Architecture

```mermaid
flowchart LR
    client([Client]) -->|HTTPS + JWT| gw[API Gateway<br/>Spring Cloud Gateway]
    gw <-->|token buckets| redis[(Redis<br/>rate limits)]
    gw -->|X-User-Id| tour[tour-core-service<br/>Spring Boot]
    gw -->|X-User-Id| book[booking-service<br/>FastAPI]
    psp([Stripe]) -->|signed webhooks| gw
    gw --> ai[ai-service<br/>FastAPI]
    tour <-->|tour + search cache| cache[(Redis<br/>cache)]
    book -->|price snapshot| tour
    book -->|Checkout, refunds| psp
    tour --> toursdb[(PostgreSQL + PostGIS<br/>tours, departure read model)]
    book --> bookdb[(PostgreSQL<br/>bookings + outbox)]
    sweeper[booking-sweeper] --> bookdb
    relay[booking-outbox-relay] -->|polls the outbox| bookdb
    relay -- availability.changed<br/>review.submitted --> mq{{RabbitMQ<br/>smarttours.events}}
    tour -- tour.created<br/>tour.updated --> mq
    mq -- availability.changed<br/>review.submitted --> tour
    mq -- tour.* --> worker[ai-worker] --> aidb[(PostgreSQL<br/>+ pgvector)]
    ai --> aidb
```

| Service | Stack | Responsibility |
| --- | --- | --- |
| **api-gateway** | Java 21, Spring Boot 4, Spring Cloud Gateway, Spring Security, Redis | Single entry point. Verifies JWTs, enforces roles per route, strips spoofed identity headers and forwards `X-User-Id` / `X-User-Roles`. Rate limits per user or IP with Redis token buckets. |
| **tour-core-service** | Java 21, Spring Boot 4, JPA + jOOQ, PostGIS, Flyway, Redis | Catalogue with optimistic concurrency (ETag / If-Match) and one search endpoint (geo, full text, facets, ratings, date availability) with keyset pagination and Redis caching. Keeps a read model of departures and ratings fed by booking events. |
| **booking-service** | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Stripe SDK | Departures with price snapshots, seat holds, Stripe Checkout, signed webhooks, cancellation, refunds and verified reviews. Every seat change writes an event to a transactional outbox; separate processes expire holds and relay the outbox. |
| **ai-recommendation-service** | Python 3.12, FastAPI, sentence-transformers, pgvector | Embeds tours from events (separate worker process). "Similar tours" and natural-language search. |

Key decisions are recorded in [`docs/adr`](docs/adr/README.md).

## Search

One endpoint, any combination of filters, public through the gateway:

```bash
# Desert tours with a departure in the first half of December that still has 2 seats, cheapest first
curl -s "localhost:8080/api/tours/search?category=DESERT&from=2026-12-01&to=2026-12-15&minSlots=2&sort=price_asc"

# Within 100 km of Windhoek, rated 4.5 or better, nearest first (distanceMeters in every hit)
curl -s "localhost:8080/api/tours/search?lat=-22.5609&lon=17.0658&radius=100000&minRating=4.5"

# Full text with web-search syntax, inside a bounding box (minLon,minLat,maxLon,maxLat), two categories
curl -s "localhost:8080/api/tours/search?q=sunrise%20dunes%20-quad&bbox=14,-26,17,-22&category=DESERT,ADVENTURE"

# Next page: send nextCursor back with the same filters
curl -s "localhost:8080/api/tours/search?category=DESERT&sort=price_asc&cursor=<nextCursor>"
```

| Parameter | Meaning |
| --- | --- |
| `lat`, `lon`, `radius` | Point and radius in metres (max 500 km). Not with `bbox`. |
| `bbox` | `minLon,minLat,maxLon,maxLat`. Must not cross the antimeridian. |
| `q` | Full text (`websearch_to_tsquery`, English stemming; the name weighs more than the description) |
| `minPrice`, `maxPrice` | NAD, inclusive |
| `category`, `difficulty` | One or more, repeated or comma-separated |
| `minDuration`, `maxDuration` | Minutes, inclusive |
| `minRating` | Average of verified reviews, 0 to 5 |
| `from`, `to`, `minSlots` | Has a departure in that date range (Africa/Windhoek days) with at least `minSlots` seats left |
| `sort` | `relevance` (default with `q`), `distance` (default with a point), `price_asc`, `price_desc`, `rating`, `newest` (default otherwise) |
| `size`, `cursor` | Page size up to 100; opaque keyset cursor from the previous page |

Contradictory or out-of-range parameters get one 400 Problem Details listing every problem. The
[OpenAPI document and Swagger UI](http://localhost:8080/docs/tours/swagger-ui.html) describe all of tour-core's
endpoints. Departure availability comes from an event-fed read model and may lag booking-service by a moment;
a booking always checks seats in booking-service itself ([ADR 0008](docs/adr/0008-outbox-and-cqrs-availability-read-model.md)).

## Booking flow

A booking is a saga of local transactions with a compensation for each step, not a distributed
transaction ([ADR 0006](docs/adr/0006-hold-then-pay-booking-saga.md)).

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant B as booking-service
    participant DB as bookings-db
    participant S as Stripe
    C->>B: POST /api/bookings
    B->>DB: lock departure, take seats, PENDING_PAYMENT (hold 30 min), commit
    B->>S: create Checkout Session (no lock held, idempotency key)
    alt Stripe fails
        B->>DB: release seats, CANCELLED
        B-->>C: 502 Problem Details
    else
        B-->>C: 201 + checkout_url
    end
    C->>S: pays (card 4242 4242 4242 4242 in test mode)
    S->>B: checkout.session.completed (signed, via gateway)
    B->>DB: dedupe event id + CONFIRMED, one transaction
    Note over B,DB: unpaid: sweeper or checkout.session.expired -> EXPIRED, seats released<br/>paid after expiry: re-reserve if free, else refund automatically
```

| Status | Meaning | Seats counted |
| --- | --- | --- |
| `PENDING_PAYMENT` | Seats held until `hold_expires_at`, checkout open | yes |
| `CONFIRMED` | Paid | yes |
| `EXPIRED` | Hold lapsed unpaid | no |
| `CANCELLED` | Cancelled (hold released, or confirmed within 48 h: no refund) | no |
| `REFUNDED` | Cancelled more than 48 h before departure, or paid too late for a seat | no |

### Endpoints (through the gateway on :8080)

| Method and path | Who | What |
| --- | --- | --- |
| `POST /api/availability` | OPERATOR | Open a departure. Validates the tour with tour-core and snapshots its price. |
| `GET /api/availability?tour_id=` | any user | Departures with `unit_price`, `currency`, `slots_remaining` |
| `POST /api/bookings` | any user | Hold seats; returns `checkout_url`. `Idempotency-Key` header makes retries safe. |
| `GET /api/bookings/me`, `GET /api/bookings/{id}` | owner | Booking, payment status, hold expiry |
| `POST /api/bookings/{id}/cancel` | owner | Release a hold, or cancel under the refund policy (explained in `cancellation`) |
| `POST /api/bookings/{id}/review` | owner | Rate a confirmed trip once it has started (1 to 5, once); feeds the tour's average in tour-core |
| `POST /webhooks/stripe` | Stripe (public, signature-verified) | Payment events |

## Engineering highlights

- **Three locking strategies, measured.** Seat reservation sits behind a strategy interface: pessimistic
  `FOR UPDATE`, optimistic version compare-and-swap, or one conditional `UPDATE`. All three pass the same
  concurrency tests, and a k6 benchmark through the gateway compares them
  ([results](docs/benchmarks/booking-locking.md)).
- **Search with measured indexes.** One jOOQ-built query over geo, full text, facets, ratings and date
  availability, keyset-paginated. On 100k tours, EXPLAIN before and after each index decided what stays:
  a keyset page at depth ~1000 reads 24 buffers (OFFSET at the same depth: 19,866), and nearest-first uses
  PostGIS KNN through a two-step query (118 ms to 0.85 ms), because a plain `ORDER BY distance, id` never does
  ([plans](docs/benchmarks/search.md)).
- **Outbox and CQRS read model.** Every seat change writes its event in the same transaction (checked after
  every test in the suite); a relay publishes with confirms, at least once; tour-core applies events by version,
  safe against duplicates and reordering ([ADR 0008](docs/adr/0008-outbox-and-cqrs-availability-read-model.md)).
- **Caching that can fail.** Redis first pages keyed by a catalogue generation (no SCAN/KEYS), jittered TTLs,
  single-flight on misses, and Redis down costs latency, not availability (tested by stopping Redis mid-test).
  On a laptop, a realistic search mix through the gateway runs at a median of 15 to 19 ms at 20 req/s and
  tops out around 80 req/s ([load test](docs/benchmarks/search.md#load-test-k6-through-the-gateway)).
- **Rate limits per caller.** Redis token buckets at the gateway keyed by JWT subject or a proxy-aware client IP,
  429 Problem Details with `Retry-After`, fail-open for reads and fail-closed for seat holds
  ([ADR 0010](docs/adr/0010-rate-limiting-at-the-gateway.md)).
- **Payments that can't lose money or seats.** Hold-then-pay saga with compensations, Stripe calls made
  outside every lock, signed and deduplicated webhooks, automatic refund when a payment arrives after its
  hold expired and the seats are gone, and a provider interface so a Namibian PSP can replace Stripe.
- **No overbooking under contention.** 50 concurrent requests for 10 seats book exactly 10, verified against real
  PostgreSQL in [`test_concurrency.py`](booking-service/tests/test_concurrency.py). A control test reproduces the
  lost update that happens without the lock, and a `CHECK` constraint backs up the application logic.
- **Idempotent writes.** `Idempotency-Key` on bookings: a retried request returns the original booking instead
  of charging twice.
- **Database per service.** Three PostgreSQL instances, each schema owned by its own migrations (Flyway / Alembic).
- **Resilient messaging.** Typed events with ids, consumer-owned quorum queues, a TTL retry queue and a
  dead-letter queue, so a malformed message can't block the pipeline.
- **Security at the edge.** Standard OAuth2 resource server (Keycloak-ready). Identity is never taken from
  request bodies.
- **Consistent errors.** RFC 9457 Problem Details (`application/problem+json`) from every service.

## Quickstart

Requirements: Docker with Compose v2.

```bash
cp .env.example .env                                   # then replace every change-me value
# a strong JWT secret:  openssl rand -base64 48
docker compose up --build
```

The first build downloads the embedding model (~90 MB) into a volume. After that, start-up takes seconds.

### Try it

```bash
OPERATOR=$(python scripts/dev_token.py --sub op-1 --roles OPERATOR)
CUSTOMER=$(python scripts/dev_token.py --sub alice)

# Create a tour (operator only)
curl -s -X POST localhost:8080/api/tours -H "Authorization: Bearer $OPERATOR" -H 'Content-Type: application/json' \
  -d '{"name":"Sossusvlei Sunrise Dunes","description":"Climb Dune 45 at dawn","price":1850.00,"latitude":-24.7275,"longitude":15.3424,"category":"DESERT","difficulty":"MODERATE","durationMinutes":300}'

# Tours within 400 km of Windhoek (public)
curl -s "localhost:8080/api/tours/nearby?lat=-22.5609&lon=17.0658&radius=400000"

# Open a departure with 10 seats (price snapshotted from tour-core), then hold 2 of them, safely retryable
curl -s -X POST localhost:8080/api/availability -H "Authorization: Bearer $OPERATOR" -H 'Content-Type: application/json' \
  -d '{"tour_id":1,"starts_at":"2026-12-01T05:30:00+02:00","total_slots":10}'
curl -s -X POST localhost:8080/api/bookings -H "Authorization: Bearer $CUSTOMER" -H 'Idempotency-Key: 7f9c' \
  -H 'Content-Type: application/json' -d '{"availability_id":1,"slots":2}'   # PENDING_PAYMENT + checkout_url

# Pay for it. With the default fake provider, send the signed webhook Stripe would send:
python scripts/fake_webhook.py --booking 1                                     # -> CONFIRMED

# Semantic search over tour descriptions
curl -s "localhost:8080/api/ai/search?q=sunrise+in+the+desert" -H "Authorization: Bearer $CUSTOMER"
```

RabbitMQ management UI: http://localhost:15672 (credentials from `.env`). For real Stripe test-mode
payments see [docs/smoke-test.md](docs/smoke-test.md#7-stripe-test-mode).

### Configuration (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `JWT_SECRET` | none, required | HS256 key shared by the gateway and `dev_token.py` |
| `*_DB_USER`, `*_DB_PASSWORD` | none, required | One database per service |
| `RABBITMQ_USER`, `RABBITMQ_PASSWORD` | none, required | Broker credentials |
| `PAYMENT_PROVIDER` | `fake` | `fake` (no account needed) or `stripe` (test mode) |
| `STRIPE_SECRET_KEY` | empty | `sk_test_...`, only for `PAYMENT_PROVIDER=stripe` |
| `STRIPE_WEBHOOK_SECRET` | none, required | Webhook signing secret: `whsec_...` from the Stripe CLI, or any random string for `fake` |
| `CHECKOUT_SUCCESS_URL`, `CHECKOUT_CANCEL_URL` | `localhost:3000/bookings/{booking_id}?...` | Where Checkout sends the customer afterwards |
| `HOLD_MINUTES` | `30` | Seat hold while paying (32 with Stripe: a session lives at least 30 minutes) |
| `CANCELLATION_FULL_REFUND_HOURS` | `48` | Full refund when cancelled earlier than this before departure |
| `RESERVATION_STRATEGY` | `atomic` | `atomic`, `pessimistic` or `optimistic`, chosen by benchmark ([ADR 0002](docs/adr/0002-pessimistic-locking-for-bookings.md)) |
| `BOOKING_WEB_CONCURRENCY` | `4` | uvicorn worker processes for booking-service |
| `RATE_LIMIT_*` | see `.env.example` | Token-bucket budgets per route ([ADR 0010](docs/adr/0010-rate-limiting-at-the-gateway.md)) |
| `RATE_LIMIT_TRUSTED_PROXIES` | empty | CIDRs of load balancers whose `X-Forwarded-For` is believed |

## Running the tests

```bash
api-gateway/mvnw -f pom.xml verify   # both Java services, no local Maven needed; Testcontainers runs PostGIS in Docker

cd booking-service
pip install -r requirements-dev.txt
docker compose up -d bookings-db
createdb -h 127.0.0.1 -p 5434 -U bookings bookings_test   # once
TEST_DATABASE_URL=postgresql+psycopg://bookings:<password>@127.0.0.1:5434/bookings_test \
TEST_RABBITMQ_URL=amqp://<user>:<password>@127.0.0.1:5672/%2F pytest   # RabbitMQ is optional locally
```

tour-core's jOOQ classes are generated from the Flyway migrations and committed (`src/main/generated`). After
changing a migration, regenerate them (needs Docker) and commit the result. CI fails when they are stale:

```bash
cd tour-core-service && ./mvnw -Pjooq-codegen generate-sources
```

No test calls Stripe or tour-core: payments use the fake provider, tour-core and Stripe's HTTP API are
mocked with respx, and webhooks are signed in the tests with a test secret. CI needs no Stripe secrets.

Benchmark the reservation strategies (needs the stack running; k6 runs in Docker):

```bash
python benchmarks/run_booking_contention.py        # writes docs/benchmarks/booking-locking.md
python benchmarks/run_search_benchmark.py --seed   # 100k tours; cold vs warm cache; a rate-limit burst
```

CI runs all of this on every push: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Make it run: fixes, per-service databases, migrations, Dockerfiles, CI, ADRs | Done |
| 1 | Booking core: payment hold → Stripe Checkout → signed webhooks → confirm/expire; locking benchmark with k6 | Done |
| 2 | Search and caching: dynamic filters, distance sort, keyset pagination, CQRS availability, Redis, rate limiting | Done |
| 3 | Integrations: currency (Frankfurter, NAD base) and weather (Open-Meteo) behind Resilience4j | |
| 4 | Async workers: tour-core outbox (booking-service's landed in Phase 2), notification-service (Celery), PDF tickets, Mailpit | |
| 5 | Production ops: Keycloak, OpenTelemetry + Grafana, live deployment with TLS | |
| 6 | AI: hybrid pgvector + PostGIS search, RAG itinerary generation | |

## Repository layout

```
api-gateway/                 Spring Cloud Gateway, security
tour-core-service/           Spring Boot, PostGIS, Flyway
booking-service/             FastAPI, SQLAlchemy, Alembic, payments, reviews, hold sweeper, outbox relay
ai-recommendation-service/   FastAPI API + RabbitMQ worker, pgvector
docs/adr/                    Architecture decision records
docs/benchmarks/             Measured results: locking under contention, search plans and load tests
benchmarks/                  k6 scenarios (booking contention, search) and the scripts that run them
scripts/seed_tours.sql       100k realistic tours and departures for search benchmarks (dev only)
scripts/dev_token.py         Mint local JWTs (until Keycloak)
scripts/fake_webhook.py      Send a signed Stripe-format webhook (fake provider)
docker-compose.yml           Full local stack
```
