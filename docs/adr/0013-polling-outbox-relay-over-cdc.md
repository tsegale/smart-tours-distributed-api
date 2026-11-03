# 13. Transactional outbox in tour-core, relayed by polling (not CDC)

Date: 2026-11-04 · Status: Accepted

## Context
tour-core published `tour.created` / `tour.updated` after its database commit (ADR 0005). A broker outage at that
moment lost the event; the AI worker's embeddings and the catalogue's consumers then stayed stale until the next
edit of that tour. booking-service closed the same gap in Phase 2 with a transactional outbox and a polling relay
(ADR 0008). tour-core is the last producer with the dual write.

An outbox needs a relay that moves committed rows to the broker. Two industry-standard ways:

1. **Polling relay**: a process claims unpublished rows (`FOR UPDATE SKIP LOCKED`), publishes them with publisher
   confirms, marks them published.
2. **Change data capture** (Debezium): read the outbox inserts from Postgres' write-ahead log and route each one to
   the broker with Debezium's outbox event router. No polling, no `published_at`, latency in milliseconds.

## Decision
**A polling relay inside tour-core**, the same design as booking-service's, so both producers behave alike:

- `outbox` table (Flyway `V6`), the same columns and partial indexes as booking-service's. `Outbox.record()` is
  `@Transactional(propagation = MANDATORY)`: it refuses to write outside the transaction that changes the tour.
  The after-commit publisher is gone; the in-process event remains only for cache invalidation (ADR 0009).
- `OutboxRelay`, a `@Scheduled` job every 500 ms: one batch of up to 100 rows per transaction, claimed
  `FOR UPDATE SKIP LOCKED`, sent with a `CorrelationData` each (`publisher-confirm-type: correlated`), marked
  `published_at` only once **every** confirm in the batch is an ack (10 s timeout). A nack, a timeout or a broker
  outage throws, the transaction rolls back and nothing is marked. A full batch is followed at once by the next.
- **No ShedLock leader**: any number of instances relay at once; a second relay skips the rows the first has
  locked. Order across batches is therefore not guaranteed (docs/events.md says so; consumers dedupe by `eventId`).
- Published rows are pruned after 7 days, 1,000 per statement.
- The same envelope and message properties as booking-service (docs/events.md): persistent, `message_id` =
  `eventId`, `type` = routing key, `timestamp` = `occurredAt`.
- **Relay lag, for both relays**: `outbox_oldest_unpublished_age_seconds` and `outbox_unpublished_events`, read from
  the table at scrape time (tour-core's actuator, booking-service's new `/metrics`). The alert to build in Phase 5:
  age above a minute.

Tests (Testcontainers Postgres + RabbitMQ, `OutboxIntegrationTest`): a rolled-back change leaves no row and a write
outside a transaction is refused; a committed change is published once, in the shared envelope; with the broker
paused, the batch times out, nothing is marked, and it is published after the broker returns; two relays draining
250 events publish each exactly once; published rows past the retention are pruned, unpublished ones kept. Event
payloads are checked against their JSON Schemas (`EventContractTest`).

### Why polling, now
- **Memory.** Debezium Server is one more JVM process (Quarkus) on an 8 GB laptop that already runs three JVMs and
  swaps under load (docs/benchmarks/integrations.md, docs/benchmarks/notifications.md). The relay is a thread in a
  process that already exists. (Not measured here: the point is one process fewer, not a number.)
- **Fewer moving parts.** CDC needs `wal_level=logical`, a publication, a replication slot that pins WAL while the
  connector is down (a disk-filling failure mode that needs its own alert), connector offsets, and an upgrade path
  for all of it. Polling needs a table and an index.
- **The volume doesn't call for it.** Tours change a few times a day; booking-service's relay, measured, publishes
  within about a second at p50 under the contention benchmark (ADR 0008). The 500 ms poll is the dominant latency
  and nobody waits on a tour edit reaching the AI worker.
- **The same design in both producers** is one thing to understand, test and operate.

### The migration path to Debezium (when one of the reasons above stops holding)
1. Postgres: `wal_level=logical`, `max_replication_slots` and `max_wal_senders` of at least 1 each, and
   `CREATE PUBLICATION smarttours_outbox FOR TABLE outbox;` in a migration. A replication role for the connector.
2. Run Debezium Server with the Postgres source (`pgoutput`, `slot.name`, `publication.name=smarttours_outbox`,
   `table.include.list=public.outbox`) and the **RabbitMQ sink**: `debezium.sink.type=rabbitmq`,
   `debezium.sink.rabbitmq.exchange=smarttours.events`, routing key taken from the record
   (`debezium.sink.rabbitmq.routingKey.source=key`). Check each property against the Debezium Server docs of the
   version you deploy.
3. The **outbox event router** SMT (`io.debezium.transforms.outbox.EventRouter`) maps the columns we already have:
   `table.field.event.id=id`, `table.field.event.key=event_type` (so the record key is the routing key),
   `table.field.event.payload=payload`, and `id` into the `message_id` header. The payload stays byte for byte what
   consumers receive today, so **no consumer changes**.
4. Cut over per service: stop its polling relay (`smarttours.outbox.poll-interval` to a disabled value, or remove
   the `@Scheduled`), start the connector at the current WAL position, let the relay's backlog drain first (the
   lag gauge reads 0), then delete rows right after insert, or keep `published_at` and prune as now. Duplicates
   during the switch are absorbed by consumers' `eventId` dedupe.
5. Replace the lag gauge with the connector's own metrics (`MilliSecondsBehindSource`) and alert on replication slot
   size.

## Consequences
- tour events are no longer lost when the broker is down: they wait in the outbox, and the lag gauge says so.
- At-least-once, as with booking-service: a crash between publish and mark republishes a batch.
- One more INSERT per tour write, and a scheduled thread polling a partial index every 500 ms (an empty index scan
  when idle). The scheduler pool grew to 3 threads, so the daily FX refresh can't delay the relay.
- Up to 500 ms of extra delay before consumers hear about a tour change.
- ShedLock (ADR 0011's FX refresh) is still used for jobs that must run once; the relay deliberately isn't one.
