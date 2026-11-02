# Integrations: the tour page while providers fail

Measured, not estimated. Reproduce with the chaos profile running:

```bash
docker compose --profile chaos --env-file .env --env-file infra/chaos/chaos.env up -d --wait
docker compose stop ai-service ai-worker booking-service booking-sweeper booking-outbox-relay  # not on this path
python benchmarks/run_integrations_benchmark.py
```

Every request is `GET /api/tours/{id}?currency=EUR&include=weather&date=<5 days ahead>` through the gateway:
the tour (cached, ADR 0009), its price converted to EUR (rates in memory, ADR 0011) and the departure weather
(Redis, else Open-Meteo through the resilience policy, ADR 0012). The providers are WireMock stubs whose faults
are switched between runs (`scripts/chaos.py`). 400 tours spread over 389 weather cells of the 100k seed, so a
cold cache really is cold. 40 requests/s for 60 s per run (k6 `constant-arrival-rate`), 200 signed-in users.

## Status: one clean round, not yet the three-round table

The runner's three-round table (between the k6 markers below) has **not been produced yet**. Four full runs were
attempted on 2026-11-02 and 2026-11-03; only one round of one run was clean. The others measured the laptop:

| Run | What went wrong | Evidence |
| --- | --- | --- |
| 1 | Docker VM swapping: p50 17.5 s on healthy stubs with a warm cache | 59 MiB free and 612 MiB swap in use in the VM before the run; hundreds of Redis timeouts a minute in tour-core |
| 2 | stopped after round 1 to investigate tails of 20 s; not reproducible alone (below) | |
| 3 | round 1 clean (below); in round 2 the WireMock stubs thrashed against their own 256 MiB limit and stopped answering | `memory.events`: `max 960`, 2,483 major page faults in the stub container; its admin API timed out |
| 4 | after giving the stubs 512 MiB and an explicit heap: stalls of up to 60 s in every scenario | host RAM 895 MB free of 8 GB (Docker Desktop, browser, other apps), so Windows pages the VM itself |

The fixes kept from this: ai-service, ai-worker and the booking processes are stopped for the run (the runner's
docstring says so), the stub container has 512 MiB and `-Xmx192m`, and the runner records the VM's free memory and
swap in the report. What is left is the host: run it again with other applications closed.

## Measured: run 3, round 1

All five scenarios in sequence, as `run_integrations_benchmark.py` runs them, on 2026-11-03 at 01:00 (Africa/Windhoek).
Docker VM before the run: 207 MiB free, 278 MiB swap in use; stubs still at 256 MiB. The raw k6 summaries of this
round were overwritten by run 4 (same file names); the figures are the runner's own output.

| Scenario | p50 (ms) | p95 (ms) | p99 (ms) | Errors | Weather ok / stale / unavailable | Breaker after |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| warm cache | 7.8 | 117.8 | 406.7 | 0 | 2,379 / 0 / 22 | CLOSED |
| cold cache, healthy stubs | 8.6 | 4,533.7 | 6,393.3 | 0 | 2,253 / 0 / 2 | CLOSED |
| provider slow (2 s), breaker disabled | 6.0 | 407.1 | 419.1 | 0 | 0 / 0 / 2,401 | DISABLED |
| provider slow (2 s), breaker allowed to open | 5.3 | 9.8 | 406.7 | 0 | 0 / 0 / 2,401 | OPEN |
| every provider dead (connection reset) | 5.9 | 11.1 | 38.4 | 0 | 0 / 0 / 2,401 | HALF_OPEN |

Weather outcomes are tour-core's own count (`weather_lookups_total`) over each run: from the cache or fetched in
time (ok), served stale, or without weather (unavailable). Every response was checked: an error is anything but a
200 carrying the tour, its EUR display price and a weatherStatus. There were none in any scenario.

## Measured: single scenarios in isolation

The two scenarios with the worst tails in run 2, run alone right afterwards with the runner's own preparation
(`prepare()`), at the same 40 requests/s. Raw summaries: `benchmarks/out/diag-*.json`.

| Scenario | Duration | Requests | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| provider slow (2 s), breaker disabled | 30 s | 1,200 | 7.7 | 409.4 | 457.1 | 636.4 | 0 |
| provider slow (2 s), breaker disabled | 60 s | 2,400 | 8.2 | 410.5 | 519.6 | 643.1 | 0 |
| every provider dead | 60 s | 2,401 | 7.6 | 96.0 | 794.6 | 1,352.3 | 0 |

A thread dump of tour-core in the middle of the 30 s slow run showed all 60 request threads idle in the pool:
40 requests/s with a slow provider kept no request thread busy for long.

## Reading the numbers

- **With every provider dead, the page is as fast as with a warm cache**: p95 11.1 ms against 117.8 ms in the same
  round, p99 38.4 against 406.7. The breaker opens within the first second, after which a weather lookup is a Redis
  miss and an immediate `CallNotPermittedException`; the EUR price comes from memory either way. That is the
  headline Phase 3 set out to prove, measured once in sequence and once alone (p95 96 ms), not yet three times.
- **A slow provider costs the include budget and no more.** With the breaker disabled, every page waits its
  400 ms for weather and answers without it: p95 407 to 411 ms and p99 419 to 520 ms in three runs. The thread-pool
  bulkhead is why the cost stops there: 8 threads are busy with the slow provider, and everything past them is
  refused at once instead of queueing.
- **Letting the breaker open removes even that**: p95 9.8 ms. Its p99 of 406.7 ms is the first second, before
  10 calls have failed.
- **The cold run's tail is not explained yet.** Weather was fetched in time for all but 2 requests, so the page
  did not wait for it longer than its budget, yet p95 was 4.5 s. In the same run the stub container was close to
  its memory limit and, one scenario later, thrashed visibly; a stalled container slows the whole VM. Until a
  clean three-round run reproduces or removes it, treat it as open.
- **Warm p99 of 407 ms** on a request that does no I/O beyond Redis is this laptop: memory was short throughout
  (see the runs above). Production-like hardware should be used before comparing absolute numbers.

| | |
| --- | --- |
| Host CPU | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (8 logical cores) |
| Host RAM | 8 GB (895 MB free during run 4) |
| Host OS | Windows 11 (10.0.26300) |
| Docker | 29.7.2 (Docker Desktop), VM with 8 vCPU and 3.7 GiB, shared by every container and k6 |
| k6 | `grafana/k6:2.3.0` |

<!-- k6:start -->
<!-- k6:end -->
