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

## Reading the numbers

The three-round table below was produced on 2026-11-03 (run 5). Rounds 2 and 3 agree closely; round 1 contains a
host stall that began during its cold-cache run and lasted into the next scenarios. The table reports medians, so the
stall shows only in the bracketed ranges:

| Round 1 symptom | Value | Rounds 2 and 3 |
| --- | ---: | ---: |
| cold cache p95 | 39,030.5 ms | 20.0 and 23.8 ms |
| slow, breaker allowed to open, p99 | 2,076.6 ms | 407.7 and 407.2 ms |
| every provider dead, p99 | 4,674.0 ms | 32.7 and 24.2 ms |

A cold cache does the same work in every round (388 cells fetched from the stubs). It cost 20 to 24 ms at p95 in
rounds 2 and 3, so the multi-second cold tails of round 1 and of run 3 below come from the host, not the fetch path.
Run 5 started with 923 MiB free in the Docker VM and 424 MiB of swap already in use; the host had about 800 MB
available.

- **With every provider dead, the page is as fast as with a warm cache**: median p95 9.9 ms against 9.4 ms, p99
  32.7 against 22.1, error rate 0. The breaker opens within the first second, after which a weather lookup is a
  Redis miss and an immediate `CallNotPermittedException`; the EUR price comes from memory either way.
- **A slow provider costs the include budget and no more.** With the breaker disabled, every page waits its 400 ms
  for weather and answers without it: p95 405.9 to 406.3 ms across the three rounds. The thread-pool bulkhead is why
  the cost stops there: 8 threads are busy with the slow provider, and everything past them is refused at once
  instead of queueing.
- **Letting the breaker open removes even that**: p95 12.5 ms. Its p99 of about 407 ms is the first second, before
  10 calls have failed.
- **A cold cache is cheap when the host is healthy**: p95 23.8 ms while filling 388 cells, every lookup answered in
  time.

## Cold cache: where the time goes (Phase 4)

Phase 3 left a cold-cache p95 of 4.5 s unexplained (run 3 below). Phase 4 added a timer for each stage of tour
detail (`tour.detail.stage`: fx, weather, write; `catalogue.cache.read` and `catalogue.cache.load` for the tour
lookup; `tour.detail.request` for the whole request inside tour-core) and ran the cold scenario for 30 s at
40 requests/s with `benchmarks/attribute_cold.py`, which reads tour-core's histograms before and after the run.
2026-11-03, chaos profile, ai and booking services stopped.

| Run | Conditions | k6 p95 / p99 (ms) | tour-core request p95 / p99 (ms) | Weather fetched / unavailable | Docker VM free before, after |
| --- | --- | ---: | ---: | --- | --- |
| A | right after a rebuild: new JVM, tour cache empty | 2,280.1 / 3,085.8 | not recorded (parser bug, fixed) | 0 / 1,085, forecast breaker opened | 358, 246 MiB |
| B | the same instance a few minutes later | 433.3 / 953.6 | 156.2 / 454.8 | 358 / 18 | 301, 253 MiB |
| C | `--fresh`: restarted (warm-up ran), tour cache emptied | 242.8 / 595.6 | 191.7 / 536.9 | 372 / 1 | 323, 235 MiB |
| D | as C, with G1 instead of the Serial collector (trial, not kept) | 1,019.6 / 1,952.8 | 784.6 / 1,888.2 | 0 / 1,180, forecast breaker opened | 293, 118 MiB |

Stages in run C, the clean cold run (p95 / p99, ms): tour lookup on a cache miss 153.2 / 341.0 (383 of 1,200
requests), Redis read of the tour 26.0 / 37.4 on a miss and 4.4 / 16.7 on a hit, weather 127.8 / 309.7, display
price 1.0 / 1.0, serialisation and write 5.3 / 24.6. Three minor GC pauses in 30 s, 128 ms on average, the longest
284 ms.

**Finding: the cold path itself is bounded; the multi-second tails are the host.**

- In a clean cold run (C) the whole request inside tour-core is 192 ms at p95, the weather stage is inside its
  400 ms include budget even at p99, and the gap to what k6 sees through the gateway is 51 ms at p95.
- In the slow runs (A and D) every stage slowed down at once, including ones that do no more work when the cache
  is cold: Redis reads of cached tours went from 4.4 to 56.8 ms at p95, database loads from 153 to 742 ms, and
  calls to the WireMock stubs, which answer at once, took 2.7 s on average and opened the forecast circuit. A
  slow stub, a slow Redis and a slow Postgres at the same moment is the Docker VM stalling (it had 118 to 358 MiB
  free), not the code. The 4.5 s of Phase 3 (run 3, VM swapping) fits the same pattern, and so do the stalled
  round 1 of run 5 and runs 1 and 4.
- **GC adds to the tail, but isn't the main cause.** The container has a 768 MiB limit, so the JVM picks the
  Serial collector (one thread, all requests stop) with a 576 MiB heap. Pauses were 49 to 210 ms on average per
  run, three in 30 s. G1 (run D) was no better here and its larger footprint makes memory pressure worse on an
  8 GB laptop; on a host with spare memory, G1 and more CPU for the container are worth measuring again.
- **The weather stage can exceed the 400 ms include budget.** The budget bounds the wait for the provider, not
  the Redis reads and writes around it, each of which may take up to its 250 ms timeout. Run A's weather p99
  was 1.3 s. Bounding the whole stage is listed in the Phase 5 to-do.

## First request after a restart (Phase 4 warm-up)

`benchmarks/measure_first_request.py`: restart tour-core, wait until its readiness probe says UP, then send five
requests for tour detail with currency and weather, each for a tour and weather cell nothing had requested before
(so every request does the same cold-cache work). Three restarts; the median per request. Chaos profile with
healthy stubs, same image, warm-up switched with `WARM_UP_ENABLED`.

| Request after ready | Warm-up off (ms) | Warm-up on (ms) |
| --- | ---: | ---: |
| 1st | 9,983 (8,482 to 29,508) | 1,293 (601 to 2,401) |
| 2nd | 169 | 573 |
| 3rd | 158 | 131 |
| 4th | 107 | 168 |
| 5th | 94 | 235 |

The warm-up sends 45 requests to the service itself (weather forecast and climate normal, tour detail with
currency and weather, plain detail, list, two searches; three rounds) before Spring Boot publishes
`ACCEPTING_TRAFFIC`, and took 11.6 s. It cuts the first visitor's wait by a factor of about 8; requests 2 to 5
are noise on this host (memory was short in both series; restart to ready took 126 s with warm-up off, longer
than with it on). Against the real Open-Meteo the first request fell from 3,750 to 406 ms, but that comparison is
confounded: the network took 3 to 7 s to open a TCP connection to Open-Meteo that evening, so every weather
lookup failed in both series and the warm-up's own failures had opened the breaker before traffic arrived.

## Earlier attempts

Four full runs on 2026-11-02 and 2026-11-03 before run 5 measured the laptop rather than the code:

| Run | What went wrong | Evidence |
| --- | --- | --- |
| 1 | Docker VM swapping: p50 17.5 s on healthy stubs with a warm cache | 59 MiB free and 612 MiB swap in use in the VM before the run; hundreds of Redis timeouts a minute in tour-core |
| 2 | stopped after round 1 to investigate tails of 20 s; not reproducible alone (below) | |
| 3 | round 1 clean except a cold p95 of 4.5 s; in round 2 the WireMock stubs thrashed against their own 256 MiB limit and stopped answering | `memory.events`: `max 960`, 2,483 major page faults in the stub container; its admin API timed out |
| 4 | after giving the stubs 512 MiB and an explicit heap: stalls of up to 60 s in every scenario | host RAM 895 MB free of 8 GB (Docker Desktop, browser, other apps), so Windows pages the VM itself |

Kept from this: ai-service, ai-worker and the booking processes are stopped for the run, the stub container has
512 MiB and `-Xmx192m`, and the runner records the VM's free memory and swap in the report.

Single scenarios run alone after run 2, with the runner's own preparation (`prepare()`), at 40 requests/s:

| Scenario | Duration | Requests | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| provider slow (2 s), breaker disabled | 30 s | 1,200 | 7.7 | 409.4 | 457.1 | 636.4 | 0 |
| provider slow (2 s), breaker disabled | 60 s | 2,400 | 8.2 | 410.5 | 519.6 | 643.1 | 0 |
| every provider dead | 60 s | 2,401 | 7.6 | 96.0 | 794.6 | 1,352.3 | 0 |

A thread dump of tour-core in the middle of the 30 s slow run showed all 60 request threads idle in the pool.

Absolute numbers are from a memory-constrained laptop; use production-like hardware before comparing them.

<!-- k6:start -->
Generated by `benchmarks/run_integrations_benchmark.py` on 2026-11-03. `GET /api/tours/{id}?currency=EUR&include=weather&date=2026-11-08` through the gateway, 40 requests/s for 60s per run (k6 `constant-arrival-rate`), 3 rounds per scenario, 200 signed-in users, 400 tours in 389 weather cells of the 100k seed. Each figure is the median of the rounds, with the range in brackets when the rounds differed. Providers are the WireMock stubs of the chaos profile.

| Scenario | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | Weather ok / stale / unavailable | Breaker after |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| warm cache | 6.1 (5.9 to 6.1) | 9.4 (8.3 to 9.8) | 22.1 (16.4 to 27.0) | 0.00% | 2400 / 0 / 0 | CLOSED |
| cold cache, healthy stubs | 6.4 (5.8 to 677.8) | 23.8 (20.0 to 39030.5) | 48.2 (27.4 to 44385.9) | 0.00% | 2400 / 0 / 0 | CLOSED |
| provider slow (2 s), before the circuit opens (breaker disabled) | 6.6 (6.1 to 6.8) | 406.2 (405.9 to 406.3) | 408.4 (408.0 to 408.6) | 0.00% | 0 / 0 / 2401 | DISABLED |
| provider slow (2 s), circuit allowed to open | 6.2 (5.7 to 6.4) | 12.5 (9.5 to 131.0) | 407.7 (407.2 to 2076.6) | 0.00% | 0 / 0 / 2400 | HALF_OPEN, OPEN |
| every provider dead (connection reset) | 6.4 (6.3 to 6.4) | 9.9 (9.4 to 682.9) | 32.7 (24.2 to 4674.0) | 0.00% | 0 / 0 / 2401 | HALF_OPEN, OPEN |

Weather outcomes are tour-core's own count (`weather_lookups_total`) over each run: answered from the cache or fetched in time (ok), served stale, or without weather (unavailable). Every response is checked: an error is anything but a 200 carrying the tour, its EUR display price and a weatherStatus.

| | |
| --- | --- |
| Host CPU | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (8 logical cores) |
| Host RAM | 8 GB |
| Host OS | Windows 11 (10.0.26300) |
| Docker | 29.7.2 (Docker Desktop) |
| Docker VM | 8 vCPU, 3.7 GiB (shared by every container and k6) |
| VM memory | before the runs 923 MiB free, 424 MiB swap in use; after 588 MiB free, 400 MiB swap in use |
| k6 | `grafana/k6:2.3.0` |

Raw k6 summaries: `benchmarks/out/integrations-*.json` (not committed).
<!-- k6:end -->
