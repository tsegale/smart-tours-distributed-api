# 7. jOOQ for dynamic search, keyset pagination with opaque cursors

Date: 2026-10-30 · Status: Accepted

## Context
`GET /api/tours/search` combines up to a dozen optional filters (radius or bounding box, full text, price,
category, difficulty, duration, rating, departures with free seats in a date range) with six sort orders.
Every combination is a different SQL statement, so it must be built at runtime. The catalogue is 100k tours,
and clients page through results.

## Decision

### jOOQ for the search query, JPA for CRUD
- **Spring Data JPA stays** for loading, saving and versioning single tours: it is the right tool there.
- **jOOQ builds the search**: only the filters a request uses end up in the SQL, each value is a bind
  parameter, and the query reads like the SQL it produces (`TourSearchRepository`).
- Rejected: JPA Criteria / Specifications (verbose, and no `<->`, `@@`, row-value comparisons or `EXISTS` over
  a non-entity table without native fragments anyway), and string-built native SQL (injection-prone and
  untestable piece by piece).

### Code generation from the real schema
`./mvnw -Pjooq-codegen generate-sources` runs every Flyway migration against a throwaway `postgis/postgis:16-3.4`
container (`testcontainers-jooq-codegen-maven-plugin`) and generates the classes from that database, so they
can't drift from the migrations.

- The classes are **committed** under `src/main/generated`. The image build (`docker build`) has no Docker
  daemon to start a codegen container, and a normal build should not need one. **CI regenerates them on every
  push and fails if the result differs** from what is committed.
- The plugin (0.0.4) was last released in 2023. It runs with current jOOQ (3.21.7), Testcontainers (1.21.4,
  which speaks the API versions Docker 29 accepts) and JDBC driver, but keeps its own Flyway 9: its runner uses
  Flyway 9 internals. The migrations are plain SQL, so any Flyway applies them identically.
- **PostGIS and full text.** jOOQ 3.21 maps the `geometry` column to `org.jooq.Geometry`, but the open-source
  edition's DSL has no `ST_*` functions or `<->` operator, and `tsvector` is generated as `Object`. These
  expressions are jOOQ plain-SQL **templates**: fixed SQL text with `{0}` placeholders that jOOQ fills with
  typed fields and bind values, e.g. `DSL.condition("ST_DWithin({0}, {1}, {2}, false)", geography, point,
  DSL.val(radius))`. No request value is ever concatenated into SQL. Everything else is the typed DSL.

### Keyset pagination
- Each sort is made **total** by a tie-breaker on `id` in the same direction: `(price, id)`, `(avg_rating, id)`,
  `(created_at, id)`, `(rank, id)`, `(distance, id)`. The next page is one row-value comparison,
  `(key, id) > (:key, :id)` (ascending) or `<` (descending), which PostgreSQL can answer from a composite index.
- Cost does not grow with depth: page 500 costs about what page 1 does. OFFSET reads and discards every
  earlier row, and under concurrent inserts it skips or repeats rows. Measured in
  [`docs/benchmarks/search.md`](../benchmarks/search.md).
- The **cursor** is base64url of a small JSON object: sort, the last row's key (as exact text: decimals,
  timestamps with microseconds, floats in their shortest round-trip form), its id, and a fingerprint (SHA-256
  prefix) of the canonical filters. A cursor replayed with different filters or a different sort is refused
  with 400 instead of returning a wrong page. It is not signed: tampering can only produce another valid page
  of the same public search.
- `size` (1 to 100) may change between pages; the fingerprint deliberately leaves it out.
- `TourSearchIntegrationTest` walks every page of every sort over 40 rows identical in every sort key, and
  checks that each row comes back exactly once and in the same order as one big page.

### Distance: KNN in two steps
PostGIS' KNN operator `<->` on `geography` returns the spherical distance in metres, and PostgreSQL answers
`ORDER BY location <-> point LIMIT n` with a GiST scan that stops after n rows. **But only when the distance is
the whole sort key.** EXPLAIN showed that `ORDER BY distance, id` (the tie-breaker keyset needs) makes it sort
every candidate instead: with a point and no radius, all 100k tours (118 ms, 7,556 buffers).

So nearest-first runs in two steps, in one SQL statement (`TourSearchRepository.nearestFirst`):
1. `candidates`: KNN by distance alone, `size + 1` rows. Every row nearer than the farthest candidate is in it,
   but rows at exactly that distance may have been cut arbitrarily.
2. every matching row at exactly that boundary distance, through the same GiST index (`ST_DWithin` with a 1 m
   margin, then equality on the same `<->` expression).

The union sorted by `(distance, id)` is exactly what a full sort would return: 0.85 ms and 78 buffers for the
same query. Cursors stay exact with ties (the tests page through 40 tours at one spot). With a radius the planner
may prefer a bitmap scan of the radius and a sort of its candidates, which the two-step form doesn't hinder.

The radius filter is `ST_DWithin(..., false)`, spherical like `<->`, so every hit's `distanceMeters` is inside
the radius. The spherical distance is within about 0.3% of the spheroidal one, irrelevant at tour scale. Deep
distance pages still scan from the nearest row (KNN can't start at a distance), so their cost grows with depth;
a radius bounds it.

### Validation
`SearchCriteria.from()` collects **every** problem before answering one 400 Problem Details with an `errors`
list: lat without lon, radius without a point, radius together with bbox, a bbox crossing the antimeridian
(`minLon > maxLon`, not supported: split it into two searches), min above max for price, duration and
dates, out-of-range values, `sort=relevance` without `q`, `sort=distance` without a point, and foreign cursors.

## Consequences
- Two persistence tools in one service. The boundary is simple: JPA for aggregates, jOOQ for read queries.
- Regenerating jOOQ classes is one command after a migration. Forgetting it fails CI, not production.
- A tie-breaker on `id` means "newest" orders same-second tours by id, not by insertion time. Harmless.
- No total count in the response: counting every match costs as much as the search itself, and keyset
  pagination does not need it.
