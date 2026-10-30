# 9. Catalogue caching: generation keys, jittered TTLs, single-flight, fail-open

Date: 2026-10-30 · Status: Accepted

## Context
Catalogue reads (one tour, and search) outnumber catalogue writes by orders of magnitude, and popular
searches repeat: "safari near Etosha" is asked far more often than any long-tail query. A search runs
several index scans and a sort over 100k tours, so answering the popular ones from memory is worth it.
The cache must never be needed, though: an outage of Redis must cost latency, not availability.

## Decision
Spring Cache on Redis (`RedisCacheManager`), JSON values through Spring Data Redis' Jackson 3
`JacksonJsonRedisSerializer`, typed per cache (no polymorphic type ids in the payload, so nothing in Redis
can make the service instantiate an arbitrary class). Accessed through one component, `CatalogueCache`, so
every Redis call is guarded the same way.

| Cache | Key | TTL | Invalidation |
| --- | --- | --- | --- |
| `tour` | `tour::{id}` | 10 min ± 10% | evicted after commit of a PUT, and after a new rating |
| `search` | `search::{generation}:{sha256(canonical search)}` | 60 s ± 10% | generation bump on any catalogue change |

- **Only first pages are cached.** Later pages are rarer and their cursors make every key unique.
- **Canonical key.** `SearchCriteria.canonical()` normalises parameter order, repeated versus comma-separated
  values, case, whitespace and `1850` versus `1850.00`, so equivalent searches share one entry. The key is the
  SHA-256 of that string, with page size included.
- **Generation keys instead of SCAN/KEYS.** A new tour, an edit or a rating runs `INCR search:generation`.
  Every cached search is then unreachable at once, in O(1), and old entries simply expire. `KEYS` blocks Redis;
  `SCAN` + `DEL` is slow and racy. The generation is read before the database, so a result computed from older
  data is stored under the older generation, where nobody looks for it.
- **Jitter.** Each entry's TTL is drawn from base ± 10%, so entries written together (after a deploy or a
  generation bump) don't all expire in the same second and send their readers to the database together.
- **Stampede protection: in-process single-flight.** Concurrent misses for one key in one instance share one
  load (`ConcurrentHashMap` of in-flight futures; `CatalogueCacheTest`). Across N instances, up to N loads of
  a key can still run at once. With a 60 s TTL, jitter and a handful of instances that is a handful of
  identical queries per minute per hot key, which the database absorbs, so no distributed lock: a Redis lock
  would add a round trip to every miss and a failure mode (lock holder dies) for little gain at this scale.
- **Fail-open.** A `CacheErrorHandler` (`FallThroughCacheErrorHandler`) logs every cache error without a stack
  trace, counts it in `cache.errors{cache,operation}` and lets the request continue against the database. Lettuce
  rejects commands immediately while disconnected, and the command timeout is 250 ms, so an outage adds at most
  that per call. `RedisOutageIntegrationTest` stops the Redis container mid-test: search, reads and a PUT all
  keep working. Redis is excluded from the health status for the same reason.
- **Metrics.** Cache statistics are on (`spring.cache.redis.enable-statistics`), so Micrometer exposes
  `cache_gets_total{cache,result}` and friends on `/actuator/prometheus`, which is reachable only on the
  internal network (the gateway routes no `/actuator` path). Grafana arrives in Phase 5.

### Availability changes do not invalidate cached searches
Seat counts change with every booking. Bumping the generation on each `availability.changed` would empty the
search cache under exactly the load it exists for. Instead:
- searches **without** date filters don't depend on seats at all, so they are unaffected;
- searches **with** `from`/`to`/`minSlots` may be up to the search TTL (60 s ± 10%) behind the read model, on
  top of the read model's own lag (ADR 0008);
- booking-service checks seats on every booking, so the worst case is a departure shown with seats that are
  gone, and a clear 409 when booking it. Nobody is oversold.

## Consequences
- A cached tour can be stale for its TTL if an eviction fails (Redis down at that moment), and in the classic
  cache-aside race (a reader loads the old row, the update commits and evicts, the reader then stores the old
  row). Both are bounded by the 10-minute TTL. ETags stay safe: a PUT with a stale ETag is checked against
  the database and gets 412.
- Every rating bumps the search generation. Reviews are rare next to searches, so this costs little; if they
  ever weren't, ratings would get their own short-TTL handling instead.
- Measured effect (hit ratio, latency cold vs warm): [`docs/benchmarks/search.md`](../benchmarks/search.md).
