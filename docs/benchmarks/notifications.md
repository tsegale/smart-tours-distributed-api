# Notifications: event to email, and the memory budget

Measured, not estimated. Phase 4 adds notification-service (ADR 0014) and its infrastructure behind the
`notifications` compose profile, and puts every container under a memory limit. This page records how fast a burst
of confirmed bookings turns into emails with PDF tickets, and what the stack costs in memory before and after.

## Delivery under a burst

`benchmarks/run_notifications_benchmark.py`, 2026-11-04: 500 `booking.confirmed` events published at once to
`smarttours.events` from a container on the compose network, each with a contact email, so each one renders a PDF
ticket (WeasyPrint), stores it in MinIO and sends one email with the PDF attached to Mailpit. Latency is the
event's `occurredAt` to the notification's `sent_at`, both from the Docker VM's clock. The send rate limit was
lifted for the run; the core services were stopped (they are not on this path).

| Worker processes | Sent | Failed | p50 (s) | p95 (s) | p99 (s) | Max (s) | Throughput (emails/s) | PDF render p50 / p95 (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 500 | 0 | 343.9 | 352.2 | 352.8 | 352.8 | 1.37 | 377 / 1,084 |
| 2 | not measured | | | | | | | |

Read the latency as queueing, not as per-email cost: a burst of 500 puts 500 `render_ticket` tasks into one FIFO
queue, each enqueues its `send_email` behind the remaining renders, so almost every email waits for almost every
render. One email costs about 0.7 s of worker time (1 / 1.37 emails/s), roughly half of it the PDF. With the
Phase 4 design that cost is the number to scale on; a separate queue for `send_email` (served first) would turn
the 344 s p50 into "render time of the emails ahead of you", and is listed under what to tighten.

What was not measured, and why:
- **2 worker processes.** Two attempts on the 8 GB laptop ended with the host at 75 to 85 MiB free and Windows
  compressing the Docker VM's memory (3.8 GB in Memory Compression, the VM's working set down to 263 MiB). Renders
  slowed from 377 ms to 1 to 4 s, so the run measured the laptop's paging, not the pipeline. It was stopped and its
  rows removed.
- **Peak memory per container during the run.** The sampler thread crashed at the end of the 1-process run (a bug
  in the script, since fixed), so only the idle figures below exist.
- The 1-process run used the image before the font cache fix (fontconfig had no cache, see the commit history);
  render times after the fix were not measured under load.

The smoke test gives the single-email case: one confirmation on a cold worker rendered its PDF in 8.6 s and was
sent 13.7 s after the bridge recorded the event (worker and bridge logs); a warm render of the same ticket takes
about 0.4 s.

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
