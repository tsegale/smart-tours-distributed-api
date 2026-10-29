# 2. Seat reservation strategy, chosen by benchmark

Date: 2026-10-27 · Revised: 2026-10-29 (Phase 1, with measurements) · Status: Accepted

## Context
Popular departures get concurrent requests for the last seats. A naive read-check-write lets two requests
both see "1 left" and both book: the *lost update* anomaly. `tests/test_concurrency.py` reproduces it.

Phase 0 chose a pessimistic lock without measuring the alternatives. Phase 1 puts seat reservation behind
a `SeatReservationStrategy` interface (`app/services/reservation.py`, `RESERVATION_STRATEGY`) and measures
all three under the same load.

## Options
1. **Pessimistic**: `SELECT … FOR UPDATE` on the departure row, then check and write.
2. **Optimistic**: read without a lock, then `UPDATE … WHERE version = :seen`. Retry with jittered backoff
   when 0 rows match, and answer a retryable 409 after 8 attempts.
3. **Atomic**: `UPDATE … SET booked_slots = booked_slots + :n WHERE id = :id AND booked_slots + :n <= total_slots
   RETURNING …`, one statement that checks and writes.

All three pass the same concurrency tests: exactly N seats sold, no lost updates when cancellations are
interleaved, and the CHECK constraint is never hit. Every writer bumps `version`, so the optimistic
compare-and-swap can't overwrite a release made by a locking path.

## Measurements
[`docs/benchmarks/booking-locking.md`](../benchmarks/booking-locking.md). k6 through the gateway: 100 VUs,
1000 attempts at one 500-seat departure, fake payment provider, 4 uvicorn workers, i5-1135G7 laptop.
Median of 3 rounds, with the strategy order rotated:

| Strategy | p50 | p95 | Throughput | 409 busy (all rounds) | Oversold |
| --- | ---: | ---: | ---: | ---: | :---: |
| atomic | 685 ms | 2969 ms | 95 req/s | 0 | never |
| pessimistic | 758 ms | 3133 ms | 91 req/s | 0 | never |
| optimistic | 1154 ms | 3937 ms | 71 req/s | 16 | never |

- **Optimistic is the worst choice for a hot row.** It had the lowest throughput in every round. In one round
  it ran out of retries: 16 customers were told "busy, retry", and that round's p95 was 8.9 s. Every losing
  compare-and-swap still queues on the row lock that the winner's UPDATE holds, then wastes the round trip.
- **Atomic and pessimistic are close.** Atomic wins the medians by about 4% throughput and 10% p50, but the
  rounds overlap (pessimistic 87 to 98 req/s, atomic 77 to 100 req/s). It saves one statement while the row
  is locked; the transaction otherwise does the same work.
- The lock is not the bottleneck at this size: the whole laptop is CPU-bound across 11 containers and k6.
  The lock is held only for a few statements because the payment provider is never called under it
  (ADR 0006). An early run with one uvicorn worker managed 50 req/s at p50 1.9 s with Postgres at about 40%
  CPU, which is why booking-service now runs 4 workers.

## Decision
- **Atomic is the default for creating holds.** It has the best median latency and throughput, the shortest
  lock hold, and no retry loop to tune.
- **Pessimistic stays for every multi-row path**: releases, webhooks and the sweeper lock the departure and
  then the booking, which a single conditional UPDATE can't do. It is also one config change away for the
  create path, if a future rule needs to read and decide under the lock (for example per-customer limits).
- **Optimistic is not used for seat inventory.** It suits rows that rarely conflict (editing a tour), not
  the hottest row in the system.
- `CHECK (booked_slots <= total_slots)` stays as the backstop for every strategy.

## Consequences
- Contended departures still serialise on one row, whatever the strategy. The benchmark shows that is fine
  at this volume. If a single departure ever needs far more, split its inventory into buckets or move to a
  reservation queue.
- Lock order is always availability → booking, so no deadlocks.
- The benchmark is reproducible (`python benchmarks/run_booking_contention.py --runs 3`). Re-run it on
  production-like hardware before reading anything into differences under 10%.
