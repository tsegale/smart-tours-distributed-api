# 10. Rate limiting at the gateway: Redis token buckets per caller, fail mode per route

Date: 2026-10-30 · Status: Accepted

## Context
Search is public and cheap to call, and expensive to answer at scale. Booking creation is worse: every
`POST /api/bookings` holds seats for 30 minutes (ADR 0006), so a script can take every seat of a departure
without paying. Limits must hold across gateway instances, so the counters cannot live in one JVM.

## Decision
- Spring Cloud Gateway's **`RedisRateLimiter`**: the token-bucket Lua script that ships with the gateway,
  one atomic Redis call per request. Budgets are the standard per-route `redis-rate-limiter.*` arguments.
- **Key**: `user:<JWT subject>` when the request is authenticated, else `ip:<client address>`. The client
  address is the TCP peer, unless the peer is in `RATE_LIMIT_TRUSTED_PROXIES` (CIDRs of a load balancer); then
  `X-Forwarded-For` is read right to left, skipping trusted hops. Without that rule, a client could send a new
  random `X-Forwarded-For` on every request and never run out of tokens.
- **Budgets** (requests/second refill, burst), all overridable in `.env`:

  | Route | Key | Refill | Burst | Redis down |
  | --- | --- | ---: | ---: | --- |
  | `GET /api/tours/search`, signed in | user | 20 | 40 | open |
  | other `/api/tours/**`, `/api/bookings/**`, `/api/availability/**`, `/api/ai/**`, signed in | user | 10 | 20 | open |
  | `/api/tours/**` and `/docs/tours/**`, anonymous | IP | 5 | 10 | open |
  | `POST /api/bookings` | user | 1 | 5 | **closed** |
  | `POST /webhooks/stripe` | none | | | not limited |

  Signed-in and anonymous traffic are separate routes, told apart with a `Header=Authorization, Bearer .+`
  predicate. That is sound only because Spring Security has already rejected any invalid token with 401
  before routing.
- **Webhooks are exempt.** Stripe retries failed deliveries for days. A 429 would only delay confirmations,
  and the endpoint is already authenticated by signature and capped at 256 KB.
- **429 is Problem Details**, with `Retry-After` (seconds until one token refills) and the limiter's
  `X-RateLimit-Remaining`, `-Burst-Capacity`, `-Replenish-Rate` and `-Requested-Tokens` headers. The built-in
  `RequestRateLimiter` filter answers an empty 429 and always fails open, so a small `RateLimit` filter
  factory wraps the same `RedisRateLimiter` and adds the body, the header and the per-route fail mode.

### Fail open or closed, per route
`RedisRateLimiter` swallows Redis errors and allows the request with `X-RateLimit-Remaining: -1`; the filter
treats that, and any error the limiter doesn't absorb, as "limiter unavailable". Lettuce rejects commands
immediately while disconnected, so a Redis that is down costs no timeout. A slow one is bounded by a 1 s command
timeout: 250 ms was tried first and timed out a healthy Redis when the gateway itself was short of CPU under the
booking benchmark ([results](../benchmarks/booking-locking.md#phase-2-transactional-outbox-atomic-strategy-before-and-after)).
- **Search and reads fail open.** They are idempotent and cacheable, tour-core protects itself with its
  connection pool, and refusing all searches because a counter store is down would turn a Redis incident into
  a full outage for every visitor.
- **Booking creation fails closed (503 Problem Details, `Retry-After: 1`).** An unthrottled client can hold
  every seat for 30 minutes, which is real, lasting damage to other customers and to revenue. A few seconds of
  "try again" while Redis restarts is the smaller harm. Paying for an existing hold is unaffected: it goes
  through the webhook route.

Both modes are tested against a Redis container that the test stops (`RateLimitRedisDownTest`).

## Consequences
- One Redis round trip per limited request at the gateway (sub-millisecond on the compose network).
- Anonymous users behind one NAT share a budget; signing in lifts them onto their own.
- Budgets are guesses until production traffic exists. The k6 burst scenario in
  [`docs/benchmarks/search.md`](../benchmarks/search.md) shows the limiter refusing a single user's excess.
- The limiter has its own Redis (`redis`, default `noeviction` policy), separate from tour-core's cache
  (`redis-cache`, bounded with LRU eviction): cache pressure or a cache flush can never drop rate-limit state.
