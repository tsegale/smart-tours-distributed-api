# 1. One database per service

Date: 2026-10-27 · Status: Accepted

## Context
The first version had tour-core-service and booking-service sharing one `tours_db` with the same credentials.
Either service could read or change the other's tables, so their schemas could never evolve independently,
and the "microservices" were really a distributed monolith.

## Decision
Each service owns a separate PostgreSQL instance, credentials and schema migrations:

| Service | Store | Why this engine |
| --- | --- | --- |
| tour-core-service | PostgreSQL + PostGIS | Geospatial radius and distance queries |
| booking-service | PostgreSQL | Row locks, CHECK constraints, transactions |
| ai-recommendation-service | PostgreSQL + pgvector | Vector similarity next to relational data |

Cross-service references are plain ids (`tour_id`) with no foreign keys. Data a service needs from another
arrives through events (for example, tour-created → embedding) or an API call.

## Consequences
- Services deploy, migrate and scale independently. A slow analytics query can't lock bookings.
- No cross-service joins or transactions. Consistency between services is eventual, and workflows that
  span services need sagas (Phase 1) and an outbox (Phase 4).
- Three databases to run locally; docker-compose hides that.
