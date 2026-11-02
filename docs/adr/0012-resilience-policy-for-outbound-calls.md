# 12. Resilience policy for outbound calls

Date: 2026-10-31 · Status: Accepted

## Context
Phase 3 makes tour-core call the public internet for the first time: Frankfurter for exchange rates
(ADR 0011) and Open-Meteo for departure weather. Neither has an SLA, both are free, and one of them sits on the
request path of the tour page. A provider that is slow is worse than one that is down: down fails fast, slow
holds a request thread for as long as we let it. With 200 Tomcat threads, a provider answering in 10 s at
20 requests/s would exhaust them in 10 seconds and take the whole catalogue down with it.

Rule: **no outbound provider may make tour-core slower or less available than it is without that provider.**

## Decision

### Library and wiring
- **Resilience4j 2.4.0 with `resilience4j-spring-boot4`**, the module built for Spring Boot 4 and Spring
  Framework 7. Spring Cloud CircuitBreaker was not used: it is a provider-neutral facade over a breaker, a time
  limiter and optionally a bulkhead, with no retry. We need retry with Retry-After, and nothing here needs
  provider neutrality, so the facade would only hide the parts we configure.
- Instances live in the registries the Boot module auto-configures, which binds their metrics to Micrometer:
  `resilience4j_circuitbreaker_state`, `..._calls_seconds`, `resilience4j_retry_calls_total`,
  `resilience4j_bulkhead_*`, `resilience4j_timelimiter_calls_total`, tagged by provider, on
  `/actuator/prometheus`. `/actuator/circuitbreakers` shows (and lets an operator change) breaker states; the
  gateway routes no actuator path, so both are internal only.
- The configs are built in code (`ProviderPolicies`) from one `smarttours.integrations.<provider>` block per
  provider, rather than `resilience4j.*` YAML, because two rules are code (which failures are retried or held
  against the provider, and Retry-After), and every number for a provider then sits next to its timeouts.
- HTTP clients are Spring HTTP interface clients (`@HttpExchange`) on `RestClient` with the JDK HTTP client,
  per provider: own connect and read timeouts, a `User-Agent` naming the service and the repository, HTTP errors
  turned into `ProviderHttpException` with Retry-After parsed (seconds or HTTP-date). Built from Boot's
  `RestClient.Builder`, so every call is in `http_client_requests_seconds`. No `RestTemplate`.

### One policy per provider, in this order
```
Retry( CircuitBreaker( TimeLimiter( ThreadPoolBulkhead( HTTP call ) ) ) )
```
Resilience4j's own recommended order. Every retry attempt passes the breaker, so an open circuit stops the
retries at once; the time limit is per attempt; the call runs on the provider's own threads.

- **Bulkhead: a thread pool per provider**, not a semaphore. A semaphore bulkhead caps concurrent calls but still
  runs them on the caller's thread, so the caller still waits for a slow provider. With a thread pool, the
  request thread only waits on a future, for as long as it chooses (below).
- **Time limiter above the read timeout.** When the limit fires, the caller gets a `TimeoutException` at once;
  the bulkhead thread is freed by the HTTP read timeout, which is therefore never longer than the limit
  (enforced when the properties bind).
- **Retry only what can succeed next time**: attempt timeouts, I/O errors (refused, reset, read timeout), 5xx,
  and 429 when its Retry-After is short enough to wait for. Exponential backoff with ±50% jitter, so a fleet of
  instances doesn't retry in lockstep. Retry-After, when present and acceptable, replaces the backoff; a longer
  one is not retried: the caller falls back instead of waiting.
- **Not retried**: other 4xx (our request is wrong), unreadable bodies (the provider changed its format), an
  open circuit and a full bulkhead (both mean back off).
- **Held against the provider** by the breaker: timeouts, I/O errors, 5xx, 429 and unreadable bodies. Not
  counted: other 4xx (our fault) and bulkhead rejections (our capacity, not their health).
- **Only idempotent GETs go through these policies.** Every call here reads public data. Retrying a call that
  changes state (charging a card, creating a resource) without an idempotency key could do it twice; booking-service
  sends Stripe idempotency keys for exactly that reason (ADR 0006), and nothing in tour-core writes to a provider.

### The numbers, and why

| | Frankfurter | Open-Meteo forecast | Open-Meteo archive |
| --- | --- | --- | --- |
| Called from | daily refresh (background) | tour page, `/weather` | `/weather` and tour page, beyond the horizon |
| Connect / read timeout | 2 s / 5 s | 500 ms / 2 s | 500 ms / 2.5 s |
| Time limit per attempt | 6 s | 2.5 s | 3 s |
| Attempts (backoff) | 4 (2 s, x2, max 30 s) | 2 (200 ms) | 2 (200 ms) |
| Longest Retry-After honoured | 60 s | 1 s | 1 s |
| Breaker window / minimum calls | 10 / 4 | 20 / 10 | 30 / 10 |
| Opens at | 50% failures, or every call slower than 5 s | 50% failures or 50% slower than 1.5 s | 50% failures or 50% slower than 2 s |
| Open for, then trial calls | 5 min, 1 | 30 s, 3 | 60 s, 3 |
| Bulkhead threads / queue | 1 / 2 | 8 / 16 | 4 / 40 |

- **Frankfurter** runs once a day off the request path, so it can afford patience: a refresh that rides out a
  minute of trouble beats a day of older rates. One thread: there is only ever one refresh.
- **Forecast** is on the tour page, which never waits for it longer than 400 ms (below), so the timeouts protect
  the bulkhead threads, not the page. Measured from Windhoek on an idle line, 15 sequential calls to the real API
  took 0.96 to 1.16 s (median 1.05 s, TCP connect 0.28 s, each a new TLS connection). A first guess of 1.2 s
  would have cut off healthy calls from this region; 2 s leaves headroom, and calls slower than 1.5 s count as
  slow for the breaker. One retry. 8 concurrent fetches and 16 queued: with a 1-hour cache each 0.1° cell and date is fetched
  at most once an hour per instance, so real misses are few; past that, the bulkhead refuses at once instead of
  queueing requests behind a slow provider.
- **Archive** calls are heavier (a week of reanalysis) and come ten at a time per climate normal (one week in
  each of ten years), so a deeper queue; normals are cached for 30 days, so they are rare.
- **How long callers wait** is decided per call site, not by the policy: tour detail with `include=weather` waits
  **400 ms** (`WEATHER_INCLUDE_BUDGET`) and then answers without weather; `GET /weather` waits **3 s**, one
  attempt and the start of a retry. A fetch that outlives the caller's wait carries on and fills the cache for the next one.

### Fallback order
Exchange rates: in-process copy, Redis, database, then no display price with the reason (ADR 0011). A provider
outage only stops new rates from arriving.

Weather, per request:
1. a fresh cache entry (Redis, forecast 1 h ± 10%, climate normal 30 days);
2. a fetch, coalesced per key and instance, awaited up to the caller's budget;
3. the expired entry, served with `stale: true` and `weatherStatus: "stale"`. Entries are kept 24 hours
   (forecasts) and 90 days (normals) past their freshness for exactly this;
4. `weather: null`, `weatherStatus: "unavailable"`, HTTP 200. The tour is always there.

A climate normal is built from the years that answered, if at least half did; the response says which years.

### Open-Meteo's terms
Open-Meteo's free API is for **non-commercial use only** (open-meteo.com/en/terms): up to 600 calls a minute,
5,000 an hour, 10,000 a day. Its data is CC BY 4.0, and the licence asks for a link next to wherever the data
is shown, so every weather answer carries `attribution` ("Weather data by Open-Meteo.com", the link and the
licence). For a commercial launch:
- subscribe to a paid plan (Standard: 1 million calls a month, a fixed monthly price) and switch the base URLs
  to `customer-api.open-meteo.com`, sending the key as an `apikey` query parameter; the API is otherwise
  identical (open-meteo.com/en/pricing). The base URLs are configuration already (`OPEN_METEO_*_BASE_URL`); the
  key needs one new property and a default query parameter in `OutboundClients`;
- keep the attribution: CC BY applies to paid use too;
- revisit the bulkhead and breaker numbers against the plan's rate limits.
Frankfurter's terms allow commercial use; each source behind its blend keeps its own terms.

## Consequences
- Each provider costs a few threads (13 in all) and a scheduler with two threads for time limits and retry waits.
  The scheduler is deliberately not a Spring bean: a `ScheduledExecutorService` bean makes Spring Boot skip its
  own task scheduler and run every `@Scheduled` method on it, which the tests caught.
- The first outbound call after start-up loads the HTTP and JSON stacks; in a fresh test JVM it took over 3 s, so
  it can miss the tour page's 400 ms budget. That is a warm-up problem, and the fallback covers it: the page
  answers without weather, and the cache is filled for the next request. A readiness check after warm-up traffic
  (as for search, docs/benchmarks/search.md) would remove it.
- Breaker state is per instance. N instances each make up to `minimum-calls` failing calls before their own
  breaker opens. At these volumes that is a handful of calls per outage, not worth a shared breaker.
- Measured effect of each failure mode on the tour page: [`integrations.md`](../benchmarks/integrations.md).
  Every failure mode is also covered by WireMock tests (`OpenMeteoWeatherProviderTest`,
  `FrankfurterExchangeRateProviderTest`, `WeatherIntegrationTest`, `FxIntegrationTest`).
