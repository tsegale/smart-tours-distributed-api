# 5. RabbitMQ topic exchange, consumer-owned queues with retry and DLQ

Date: 2026-10-27 · Status: Accepted

## Context
Events went through a direct exchange whose queue was declared by the *producer*. The consumer requeued
every failure immediately, forever, so one malformed message blocked the queue (a poison message).
The JSON payload was built with `String.format`, so a quote in a tour name produced invalid JSON.

## Decision
- One durable topic exchange, `smarttours.events`, with routing keys `<aggregate>.<event>` (`tour.created`).
  The producer declares only the exchange.
- Each consumer owns its queue (quorum type), bindings, a TTL retry queue and a dead-letter queue.
  Transient failures retry up to 5 times, 10 s apart. Malformed messages go straight to the DLQ.
- Events are typed records serialised by Jackson, each with an `eventId` that consumers use for idempotency.
- Events publish after the database commit. Phase 4 replaces this with a transactional outbox to close the
  remaining dual-write gap.

## Why not Kafka
The workload is task-style messaging at modest volume, with no need for log replay or stream processing.
RabbitMQ's per-message acknowledgement, retry and DLQ features fit it directly.
Kafka would add partitions, consumer-group rebalancing and offset management for no benefit here.
