# 2. Pessimistic row locking for seat inventory

Date: 2026-10-27 · Status: Accepted

## Context
Popular departures get concurrent requests for the last seats. A naive read-check-write lets two requests
both see "1 left" and both book: the *lost update* anomaly. `tests/test_concurrency.py` reproduces it.

## Options
1. **Pessimistic lock**: `SELECT … FOR UPDATE` on the departure row, then check and write.
2. **Optimistic lock**: a `version` column; retry when the update hits 0 rows.
3. **Atomic conditional update**: `UPDATE … SET booked = booked + n WHERE booked + n <= total RETURNING *`.

## Decision
Use option 1 now. It is simple to reason about and correct under any isolation level. Requests for different
departures don't block each other, and `lock_timeout` (5 s) bounds queueing on a hot departure.
A `CHECK (booked_slots <= total_slots)` constraint backs it up, so a buggy code path still cannot oversell.

## Consequences
- Contended departures serialise. That's fine at tour-booking volumes, and is measured in Phase 1, which
  benchmarks all three options with k6 and records p95 latency and throughput here.
- Lock order is always availability → booking (create and cancel), so no deadlocks.
