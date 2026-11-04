# Notifications: event to email, and the memory budget

Measured, not estimated. Phase 4 adds notification-service (ADR 0014) and its infrastructure behind the
`notifications` compose profile, and puts every container under a memory limit. This page records how fast a burst
of confirmed bookings turns into emails with PDF tickets, and what the stack costs in memory before and after.

## Delivery under a burst

**Not measured yet.** The run (500 `booking.confirmed` events, 1 and 2 worker processes) was stopped twice on
2026-11-04 before it finished, both times because the 8 GB laptop ran critically low on memory with the
notifications profile up. One measured data point so far, from the smoke test: a single confirmation on a cold
worker rendered its PDF in 8.6 s and was sent 13.7 s after the bridge recorded the event (worker and bridge logs).

<!-- bench:start -->
<!-- bench:end -->

## Memory budget

`benchmarks/memory_footprint.py` (`docker stats --no-stream`, cgroup usage including page cache), stack idle,
2026-11-03 and 2026-11-04.

| | Before Phase 4 | After Phase 4 |
| --- | ---: | ---: |
| Default `docker compose up` | 2,248 MiB, 13 containers (the AI services were part of it) | 1,204 MiB, 10 containers |
| With `--profile notifications` | | 1,990 MiB, 18 containers |
| With `--profile ai` | | the default plus about 890 MiB (ai-service 409, ai-worker 443, ai-db 36, measured before) |

The default fell by 1,044 MiB because the AI services (the embedding model, about 850 MiB between the API and the
worker) moved behind the `ai` profile; the core services themselves are within noise of before (tour-core 491 and
426 MiB, booking-service 323 and 280). "Before" was taken soon after start-up and "after" on a warmed stack, so
treat differences of a few tens of MiB per container as noise.

The notifications profile adds 786 MiB:

| Container | Used (MiB) | Limit (MiB) |
| --- | ---: | ---: |
| notification-worker (Celery, 2 prefork processes) | 267 | 512 |
| minio | 119 | 384 |
| notification-beat | 88 | 128 |
| notification-service (API) | 84 | 160 |
| notification-bridge | 81 | 128 |
| booking-ticket-listener | 61 | 128 |
| notifications-db | 48 | 192 |
| mailpit | 40 | 128 |

Every container now has a limit, the data stores included (they had none). Limits are what a container may use,
not what it uses: the default profile's limits add up to 4,096 MiB against 1,204 MiB in use.

### Fewer Postgres servers in dev?

`benchmarks/measure_postgres_consolidation.py`: bookings-db, ai-db and notifications-db as they run (one server
each), against one `pgvector/pgvector:pg16` server holding the same three databases (restored from `pg_dump` of the
live ones), each with its own owner, `CONNECT` revoked from `PUBLIC` (checked: booking-service's user is refused by
the ai database), the same warm-up read of every table, and as many idle client connections as the live services
held. tours-db stays separate either way: it is the only one that needs PostGIS. Median of 10 samples after 30 s.

| | Memory (MiB) |
| --- | ---: |
| three servers | 139 (bookings-db 68, ai-db 47, notifications-db 25) |
| one server, three databases | 98 |
| saving | **41 MiB** |

**Recommendation: keep one server per service.** 41 MiB is 2% of the notifications stack's 1,990 MiB, and the
consolidated server would put three services in one failure domain (one `max_connections`, one restart, one
upgrade) and need a combined image to include tours-db (PostGIS and pgvector together, a custom build). ADR 0001's
guarantees could be kept on a shared server, but the memory doesn't pay for the coupling. The measurement ran with
the AI and notification services stopped (0 connections to their databases, 7 to bookings); with every pool
connected it was not measured.
