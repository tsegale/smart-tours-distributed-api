# Architecture Decision Records

Short records of the decisions that shaped this system: the context, what was decided, and what it costs.
Format: [Michael Nygard's template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-database-per-service.md) | One database per service | Accepted |
| [0002](0002-pessimistic-locking-for-bookings.md) | Seat reservation strategy, chosen by benchmark | Accepted (revised in Phase 1) |
| [0003](0003-pgvector-over-chromadb.md) | pgvector instead of ChromaDB for embeddings | Accepted |
| [0004](0004-gateway-as-oauth2-resource-server.md) | Gateway authenticates; services trust its identity headers | Accepted |
| [0005](0005-rabbitmq-topic-exchange-with-dlq.md) | RabbitMQ topic exchange, consumer-owned queues with retry and DLQ | Accepted |
| [0006](0006-hold-then-pay-booking-saga.md) | Hold-then-pay booking saga with a pluggable payment provider | Accepted |
| [0007](0007-jooq-search-with-keyset-pagination.md) | jOOQ for dynamic search, keyset pagination | Accepted |
| [0008](0008-outbox-and-cqrs-availability-read-model.md) | Transactional outbox in booking-service, event-fed availability read model | Accepted |
| [0009](0009-catalogue-caching-strategy.md) | Catalogue caching: generation keys, jitter, single-flight, fail-open | Accepted |
| [0010](0010-rate-limiting-at-the-gateway.md) | Rate limiting at the gateway: Redis token buckets, fail mode per route | Accepted |
