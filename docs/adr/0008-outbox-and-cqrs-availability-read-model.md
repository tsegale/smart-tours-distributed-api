# 8. Transactional outbox in booking-service, and an event-fed availability read model in tour-core

Date: 2026-10-30 · Status: Accepted

## Context
Search must answer "tours with a departure between these dates that still has 2 seats". Seats live in
booking-service's database, and ADR 0001 forbids tour-core from reading it. The options were a synchronous
call per search (booking-service on the hot path of every search, N calls or a batch API, and search
down whenever booking is) or a local copy kept up to date by events (CQRS: a read model).

A read model is only as good as its events. Publishing after commit, as tour-core does, loses an event
whenever the broker is unreachable at that moment (the dual-write problem, ADR 0005). Lost seat events would
leave search showing sold-out departures as available, or the reverse, until the next change. So the outbox
planned for Phase 4 was pulled forward for booking-service.

## Decision

### Outbox (booking-service)
- Table `outbox` (`id` uuid = the event's `eventId`, `aggregate_type`, `aggregate_id`, `event_type` = routing
  key, `payload` jsonb, `occurred_at`, `published_at`). Migration `0003_outbox`.
- **Every change to seat counts writes `availability.changed` in the same transaction**: creating a departure,
  all three reservation strategies, cancellations and refunds, the saga's compensation, the hold sweeper, both
  release webhooks and the late-payment re-reserve. One call, `outbox.availability_changed()`, from each path.
  An autouse fixture in `tests/conftest.py` checks after **every test in the suite** that each departure's
  newest event carries its current version and seat count, so a new path that forgets the call fails the build.
- `tour_availability.version` (added in Phase 1 for the optimistic strategy) already increases on every seat
  change. It is the event's `version`, so consumers need no extra bookkeeping to order events.
- `review.submitted` (section 2 of Phase 2) goes through the same outbox, with the review in one transaction.
- Relay: `python -m app.jobs.outbox_relay`, its own compose service. Per batch, in one transaction: claim the
  oldest unpublished rows `FOR UPDATE SKIP LOCKED`, publish each with **publisher confirms**, set
  `published_at`, commit. A full batch is followed immediately by the next; otherwise it polls every 0.5 s.
  Published rows are pruned after 7 days in batches of 1000.
- Guarantees, each covered by `tests/test_outbox_relay.py`: a rolled-back change leaves no row; two (and four)
  concurrent relays never publish the same event twice; a crash between publish and mark causes a duplicate,
  never a loss; a broker failure marks nothing.

**At-least-once, not exactly-once.** Consumers must tolerate duplicates and, with several relays,
reordering: one relay's batch can overtake another's.

### Read model (tour-core)
- `tour_departure_summary(availability_id PK, tour_id, starts_at, total_slots, slots_left, source_version,
  updated_at)`, fed from queue `core.availability-summary` (quorum, TTL retry queue, DLQ: the topology of
  ADR 0005).
- Applied in **one upsert**: `INSERT ... ON CONFLICT (availability_id) DO UPDATE ... WHERE
  source_version < EXCLUDED.source_version`. Duplicates and overtaken events change nothing, with no
  processed-events table and no lock beyond the row.
- Ratings (`review.submitted`, queue `core.tour-rating`) are not idempotent by nature (adding a rating twice
  counts it twice), so they are deduplicated by `eventId` in `processed_events`, in the same statement
  (a data-modifying CTE) as the `UPDATE tours SET rating_sum = rating_sum + :r, rating_count = rating_count + 1`.

## Consequences
- **Search is eventually consistent.** A seat change reaches search after commit + relay poll (at most
  0.5 s idle) + broker + consumer: normally well under a second; longer while the relay or broker is down,
  and then it catches up from the outbox. Cached first pages add up to their TTL on top (ADR 0009).
- **booking-service stays the source of truth.** Search may show a departure that has just sold out; the
  booking request is then refused there with 409 `not-enough-slots`. Nobody can be oversold through a stale
  read model, because nothing writes seats from it.
- The outbox adds one INSERT to each seat-changing transaction. Measured on the contention benchmark: see the
  "Phase 2: transactional outbox" section of [`docs/benchmarks/booking-locking.md`](../benchmarks/booking-locking.md).
- tour-core still publishes `tour.created` / `tour.updated` after commit, with the known dual-write gap. Its
  outbox remains Phase 4. A lost tour event is repaired by the next update of that tour.
- One more process to run (`booking-outbox-relay`), and a table that grows with traffic until pruned.
- Not done: LISTEN/NOTIFY to wake the relay instead of polling; a relay lag metric (oldest unpublished
  `occurred_at`) for Phase 5's dashboards.
