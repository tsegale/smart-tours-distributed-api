# Events

The source of truth for every integration event in Smart Tours. Each event has a JSON Schema (draft 2020-12) in
[`docs/events/`](events/); producers' tests validate what they publish against it, so a payload cannot drift from
what is written here without failing a build.

## Transport

- One durable topic exchange, `smarttours.events`. The routing key is the event type, `<aggregate>.<event>`
  (ADR 0005).
- Producers publish only through a **transactional outbox**: the event row commits with the change, and a relay
  publishes it with publisher confirms afterwards (booking-service: ADR 0008, tour-core: ADR 0013). Delivery is
  **at-least-once**: a relay that crashes after the broker confirmed but before it marked the row publishes the
  row again.
- Messages are persistent, `content_type: application/json`, `message_id` = `eventId`, `type` = the routing key,
  `timestamp` = `occurredAt`.
- A message written inside a trace has a W3C `traceparent` header, so the consumer continues that trace (ADR 0017).
  It isn't part of the event: a consumer must work without it.
- Each consumer owns its queue (quorum), bindings, a TTL retry queue and a dead-letter queue (ADR 0005).

## Envelope

Every payload is one flat JSON object with these three fields next to its own:

| Field | Type | Meaning |
| --- | --- | --- |
| `eventId` | UUID | Unique per event. **Consumers deduplicate on it**: the same event can arrive twice. |
| `eventType` | string | The routing key, e.g. `booking.confirmed`. |
| `occurredAt` | RFC 3339 date-time | When the change committed, by the producer's clock. |

## Compatibility rules

- **Additive changes** (a new optional field, a new event type) need no version change. Consumers must ignore
  fields they don't know; the schemas forbid undocumented fields for producers only, so adding one means updating
  the schema first.
- **Breaking changes** (removing or renaming a field, changing a type or meaning) bump `schemaVersion` on the
  events that carry one, and the producer publishes both versions until every consumer has moved.
- Order is not guaranteed across a relay's batches. Events whose order matters carry their own: `version` on
  `availability.changed`, `occurredAt` and the status pair on the booking lifecycle.

## Catalogue

| Routing key | Producer | Consumers | Schema |
| --- | --- | --- | --- |
| `tour.created` | tour-core | ai-worker (`ai.tour-embedding`) | [tour-event](events/tour-event.schema.json) |
| `tour.updated` | tour-core | ai-worker (`ai.tour-embedding`) | [tour-event](events/tour-event.schema.json) |
| `availability.changed` | booking-service | tour-core (`core.availability-summary`), ai-worker (`ai.tour-embedding`) | [availability-changed](events/availability-changed.schema.json) |
| `review.submitted` | booking-service | tour-core (`core.tour-rating`), ai-worker (`ai.tour-embedding`) | [review-submitted](events/review-submitted.schema.json) |
| `booking.confirmed` | booking-service | notification-service (`notifications.booking-events`) | [booking-lifecycle](events/booking-lifecycle.schema.json) |
| `booking.cancelled` | booking-service | notification-service | [booking-lifecycle](events/booking-lifecycle.schema.json) |
| `booking.refunded` | booking-service | notification-service | [booking-lifecycle](events/booking-lifecycle.schema.json) |
| `booking.hold_expired` | booking-service | notification-service | [booking-lifecycle](events/booking-lifecycle.schema.json) |
| `ticket.rendered` | notification-service | booking-service (`booking.tickets`) | [ticket-rendered](events/ticket-rendered.schema.json) |

### `tour.created`, `tour.updated`

An operator created or replaced a tour. Written in the tour's transaction.

```json
{"eventId": "6f0f3c1e-1c55-4b8e-9d0e-3f8f0a6d2b10", "eventType": "tour.updated", "occurredAt": "2026-11-03T09:12:44.120Z",
 "tourId": 42, "name": "Sossusvlei Sunrise Dunes", "description": "Climb Dune 45 at dawn",
 "meetingPoint": "Sesriem gate", "timeZone": "Africa/Windhoek", "version": 3, "latitude": -24.733, "longitude": 15.293,
 "price": 1850.00, "currency": "NAD", "category": "NATURE", "difficulty": "MODERATE", "durationMinutes": 240}
```

`version` is the tour's own version: the AI service's read model applies an event only if it is newer, so a duplicate or an
overtaken event changes nothing. The fields from `version` on were added in Phase 6; the additions are backward compatible.

`timeZone` is an IANA region id: the zone the tour's departures are in. `meetingPoint` may be null.

### `availability.changed`

A departure was opened or its seat count changed. Written in the same transaction as every seat change.

```json
{"eventId": "…", "eventType": "availability.changed", "occurredAt": "…",
 "availabilityId": 7, "tourId": 42, "startsAt": "2030-12-01T04:00:00+00:00", "totalSlots": 12, "slotsLeft": 9,
 "version": 5}
```

Apply only if `version` is newer than the one you hold; duplicates and overtaken events then change nothing.

### `review.submitted`

```json
{"eventId": "…", "eventType": "review.submitted", "occurredAt": "…", "reviewId": 3, "bookingId": 18, "tourId": 42,
 "rating": 5}
```

Not idempotent by nature (adding a rating twice counts it twice): deduplicate on `eventId`.

### Booking lifecycle: `booking.confirmed`, `booking.cancelled`, `booking.refunded`, `booking.hold_expired`

One event per status change, written in the transaction that makes it (every call to `transition()` in
booking-service; a suite-wide test checks that no path skips it). Everything a notifier needs is in the payload,
snapshotted when the departure was opened and the booking made, so no consumer calls back.

| `eventType` | `status` | When | `reason` values |
| --- | --- | --- | --- |
| `booking.confirmed` | `CONFIRMED` | payment captured | `payment_succeeded`, `late_payment_seats_rereserved` |
| `booking.cancelled` | `CANCELLED` | cancelled without a refund | `customer_cancelled_hold`, `customer_cancelled_no_refund`, `async_payment_failed`, `checkout_failed` |
| `booking.refunded` | `REFUNDED` | a full refund was decided (the provider call may still be pending) | `customer_cancelled_full_refund`, `late_payment_sold_out`, `paid_after_cancellation` |
| `booking.hold_expired` | `EXPIRED` | the seat hold lapsed unpaid | `hold_expired`, `checkout_expired` |

```json
{"eventId": "…", "eventType": "booking.confirmed", "occurredAt": "2026-11-03T10:01:07.311+00:00",
 "schemaVersion": 1,
 "bookingId": 18, "reference": "ST-7K3Q9P2M", "userId": "alice", "contactEmail": "alice@example.com",
 "status": "CONFIRMED", "previousStatus": "PENDING_PAYMENT", "reason": "payment_succeeded",
 "slots": 2, "unitPrice": "1850.00", "totalAmount": "3700.00", "currency": "NAD",
 "departure": {"availabilityId": 7, "tourId": 42, "tourName": "Sossusvlei Sunrise Dunes",
               "meetingPoint": "Sesriem gate", "timeZone": "Africa/Windhoek",
               "startsAt": "2030-12-01T04:00:00+00:00"}}
```

- `contactEmail` is null when the customer's token had no `email` claim: send nothing.
- Money is an exact decimal string in `currency` (NAD); never parse it into a float.
- `booking.refunded` adds `refund: {amount, currency}`; `booking.hold_expired` adds `holdExpiresAt`.
- Show `departure.startsAt` in `departure.timeZone`.

### `ticket.rendered`

The PDF e-ticket of a confirmed booking is in object storage. booking-service records it so the owner can download
it (`GET /api/bookings/{id}/ticket`), without reading notification-service's database.

```json
{"eventId": "…", "eventType": "ticket.rendered", "occurredAt": "…", "schemaVersion": 1,
 "bookingId": 18, "reference": "ST-7K3Q9P2M", "bucket": "tickets", "objectKey": "tickets/18/ST-7K3Q9P2M.pdf",
 "contentType": "application/pdf", "sizeBytes": 48213, "sha256": "…"}
```
