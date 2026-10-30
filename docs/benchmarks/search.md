# Search: query plans and load test

Measured, not estimated. Reproduce with `python benchmarks/explain_search.py` (plans) and
`python benchmarks/run_search_benchmark.py` (load), with the stack running and `scripts/seed_tours.sql` loaded.

## Data and machine

- **100,031 tours** (100,000 seeded plus a few dozen from earlier smoke tests): 75% clustered around 34 real hubs
  across Namibia, Botswana, South Africa and Zambia, 25% spread over the four countries' bounding boxes; 85% rated.
- **199,131 departures** in the read model (`tour_departure_summary`), 0 to 4 per tour over 120 days,
  31,845 sold out.
- tours-db is `postgis/postgis:16-3.4` with default settings, inside the compose stack.

| | |
| --- | --- |
| Host CPU | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (8 logical cores) |
| Host RAM | 8 GB (under memory pressure during these runs: well under 1 GB free) |
| Host OS | Windows 11 (10.0.26300) |
| Docker | 29.7.2 (Docker Desktop), VM with 8 vCPU and 3.7 GiB, shared by every container |

Absolute times on this laptop moved by up to 5x between runs of the same plan. Each figure below is therefore the
**median of 5 runs** after a warm-up, and the **buffer counts** (pages touched, which don't depend on load) are
the more reliable comparison.

## Method

`benchmarks/explain_search.py` runs the SQL that `TourSearchRepository` renders (captured from the database's
statement log, bind values inlined) under `EXPLAIN (ANALYZE, BUFFERS)`. Each variant runs inside
`BEGIN; DROP INDEX / CREATE INDEX ...; ...; ROLLBACK`, so "before" and "after" share one database and nothing is
changed for real. The study ran on the V1 to V3 schema; V4 adds the indexes that won.

## Results

| Query | Indexes | Median (ms) | Buffers |
| --- | --- | ---: | ---: |
| **1. radius 200 km + price ≤ 1500, nearest first** | two-step KNN (shipped) | 39.3 | 4,521 |
| | one statement `ORDER BY distance, id` | 52.8 | 4,445 |
| | two-step, without the geography GiST | 1,571.6 | 14,643 |
| **1b. point only, nearest first** | two-step KNN (shipped) | **0.85** | **78** |
| | one statement `ORDER BY distance, id` | 118.4 | 7,556 |
| | two-step, without the geography GiST | 1,973.8 | 15,064 |
| **2. full text "game drive" + 2 categories + rating ≥ 4.5** | GIN (V2) | 12.2 | 2,539 |
| | without GIN | 92.3 | 6,770 |
| **3. Namibia bbox + departure with 2 seats in 2 weeks, cheapest first** | V1 to V3 | 175.8 (sort spills to disk) | 7,170 |
| | + `(price, id)` | **0.66** | **637** |
| | + GiST geometry + `(price, id)` | 0.62 | 637 |
| | departure index `(tour_id, starts_at) INCLUDE (slots_left)` removed | 156.9 | 8,698 |
| | partial `(tour_id, starts_at) WHERE slots_left > 0` instead | 108.7 | 7,190 |
| **3b. small map viewport (1,218 tours), best rated first** | V1 to V3 | 45.3 | 6,682 |
| | + GiST geometry | 4.9 | 1,127 |
| | + `(avg_rating, id)` | 2.1 | 1,404 |
| **3c. sparse viewport (144 tours), cheapest first** | V1 to V3 | 28.7 | 6,682 |
| | + `(price, id)` only | 15.6 | 20,572 |
| | + GiST geometry + `(price, id)` | **0.32** | **148** |
| **4a. page ~1000 by keyset** `(price, id) > (4000.00, 50000)` | V1 to V3 | 59.9 | 6,712 |
| | + `(price, id)` | **0.14** | **24** |
| **4b. the same depth by `OFFSET 20000`** | V1 to V3 | 205.8 (sort spills) | 6,770 |
| | + `(price, id)` | 23.6 | 19,866 |
| **5. best rated first, no filter** | V1 to V3 | 84.4 | 6,768 |
| | + `(avg_rating, id)` | 0.04 | 20 |
| **6. newest first, no filter** | V1 to V3 | 79.3 | 6,770 |
| | + `(created_at, id)` | 0.04 | 21 |

## What the plans show

**Distance sort needs two steps to use KNN.** PostgreSQL answers `ORDER BY location <-> point LIMIT n` with a
GiST "Order By" index scan that stops after n rows, but only when the distance is the whole sort key. With the
`id` tie-breaker keyset paging needs, it sorts every candidate instead: with no radius, all 100k tours.

```
one statement, ORDER BY distance, id                two steps (TourSearchRepository.nearestFirst)
Limit (118 ms)                                      Limit (0.78 ms)
  Gather Merge                                        CTE candidates
    Sort  Key: distance, id  top-N heapsort             Limit
      Parallel Seq Scan on tours  rows=33344 x3           Index Scan using idx_tours_location_geog
                                                            Order By: location::geography <-> point
                                                      Sort  Key: sort_key, id
                                                        HashAggregate (union)
                                                          CTE Scan on candidates  rows=21
                                                          Index Scan using idx_tours_location_geog  (ties)
                                                            Index Cond: location && _st_expand(point, $1 + 1)
                                                            Filter: distance = $2
```

Step one takes the 21 nearest by distance alone. Step two adds every row at exactly the boundary distance,
found through the same GiST index. The union, sorted by `(distance, id)`, is exactly what the full sort would
return, so cursors stay exact even with many tours at one spot (`TourSearchIntegrationTest` pages through 40
tours with identical coordinates). With a 200 km radius the planner still prefers a bitmap scan of the radius
plus a sort of the ~1,500 candidates, which is cheap; the two-step form costs nothing there.

**Sort indexes turn every sorted page into a range scan.** Without them, each page sorts every match
(`external merge Disk: 6096kB` for a country-sized box). With `(price, id)` the bbox query walks the index in
price order, checks the box and the departure `EXISTS` per row, and stops at 21 matches (637 buffers). Keyset
pages are a single `Index Cond: ROW(price, id) > ROW(...)` scan of 21 rows at any depth (24 buffers at page
~1000). OFFSET with the same index still reads and discards 20,021 rows (19,866 buffers, 170x the time).

**The geometry GiST matters for small or sparse boxes.** The V1 GiST is on `location::geography`, which can't
answer a geometry `&&` test. For a whole country (half the catalogue) the planner rightly ignores any spatial
index. In a sparse viewport sorted by price, though, the price index alone walks 20,572 pages to find 21 tours
in the box; with the geometry GiST the box is resolved first (148 pages).

**Full text needs its GIN index**: 2,539 against 6,770 buffers, 12 ms against 92 ms. The rank sort runs over the
964 matching rows only.

**The departure read model's index stays as V3 created it.** The `EXISTS` probe is an index-only scan on
`(tour_id, starts_at) INCLUDE (slots_left)`, about 3 pages per candidate. A partial index on open departures did
no better, because the probe already filters `slots_left` from the index tuple.

## Kept and rejected

| Index | Migration | Kept because |
| --- | --- | --- |
| GiST `location::geography` | V1 | radius filter and KNN; 1.6 to 2 s without it |
| GIN `search_vector` | V2 | full text: 7.5x faster, 2.7x fewer buffers |
| `(tour_id, starts_at) INCLUDE (slots_left)` on departures | V3 | index-only `EXISTS` probe |
| GiST `location` (geometry) | V4 | sparse viewports: 15.6 to 0.3 ms |
| `(price, id)`, `(avg_rating, id)`, `(created_at, id)` | V4 | sorted pages and keyset in tens of pages instead of thousands |
| partial `(tour_id, starts_at) WHERE slots_left > 0` | rejected | no better than V3's index for the probe |
| B-tree on `category` / `difficulty` | not tried | 8 and 4 values: every value matches 12% or more of the catalogue, a seq scan or a sort index wins |

The four V4 indexes cost write amplification on `tours`, which is written rarely (operator edits and ratings).

## Load test (k6 through the gateway)

<!-- k6:start -->
Generated by `benchmarks/run_search_benchmark.py` on 2026-10-30, after the tightening commits (query computed once,
dedicated cache Redis, 20 s unreported warm-up of the services before each pair of runs). Catalogue: 100,037
tours, 199,134 departures. Open model (k6 `constant-arrival-rate`), 60 s per run, 200 signed-in users, through
the gateway. A paging action makes up to 4 requests, so requests/s is above actions/s.

| Load | Run | p50 (ms) | p95 (ms) | p99 (ms) | first page p95 | later pages p95 | Throughput (req/s) | 200 | Errors | Dropped | Search cache hits |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 15 actions/s | cold (cache emptied first) | 18.6 | 63.7 | 119.3 | 54.9 | 89.9 | 22 | 1308 | 0 | 0 | 60.4% (544 / 901) |
| 15 actions/s | warm (straight after) | 15.0 | 93.0 | 1563.8 | 86.5 | 93.0 | 21 | 1281 | 0 | 0 | 66.7% (601 / 901) |
| 60 actions/s | cold (cache emptied first) | 228.5 | 1580.2 | 3032.3 | 1678.8 | 1389.3 | 81 | 4922 | 0 | 68 | 85.5% (2916 / 3409) |
| 60 actions/s | warm (straight after) | 194.9 | 2168.9 | 3222.8 | 2186.1 | 2147.7 | 82 | 4956 | 0 | 136 | 85.9% (2905 / 3382) |

Rate-limit burst: one user, 100 searches/s for 10 s against a budget of 20/s with a burst of 40: **226 and 240
answered 200, 774 and 761 refused with 429** in the two sessions (20/s refill plus the 40-token burst over 10 s
is 240).

| | |
| --- | --- |
| Host CPU | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (8 logical cores) |
| Host RAM | 8 GB (under 0.2 GB free during the runs) |
| Host OS | Windows 11 (10.0.26300) |
| Docker | 29.7.2 (Docker Desktop) |
| Docker VM | 8 vCPU, 3.7 GiB (shared by 13 containers and k6) |
| k6 | `grafana/k6:2.3.0` |

Raw k6 summaries: `benchmarks/out/search-*.json` (not committed).
<!-- k6:end -->

### Reading the numbers

- **Capacity on this laptop is about 80 searches/s** of this mix (60 actions/s), where the p95 climbs past 1.5 s
  and a few iterations are dropped. At 15 actions/s the median search takes 15 to 19 ms.
- **Cold versus warm cache barely differs** at these rates: popular searches repeat so often that a freshly
  emptied cache is 60% (15/s) to 85% (60/s) hits within the first minute. The cache earns its keep in absorbing
  the head of the distribution, so capacity goes to the long tail.
- **A cold service is far worse than a cold cache.** The first runs of this benchmark started right after a
  restart and measured mostly JVM warm-up: p50 3.0 s and 16 to 26 req/s. Probed one shape at a time, the first
  search of each shape after a restart took 0.1 to 4.8 s (up to 15 s for the very first one), the same shapes
  50 to 130 ms afterwards. The runner now warms up for 20 s before measuring; a deployment should do the same
  (readiness after warm-up traffic) before taking real load.
- **One query was doing redundant work per row**: inline in `ts_rank_cd(...)`, `websearch_to_tsquery` was
  evaluated for every matching row under a generic (prepared) plan. As a function in `FROM` it runs once:
  111 to 57 ms for a query matching 18,655 tours.
- **The dense radius query stays as planned.** For 200 km around the Okavango, the GiST index scan the KNN
  operator could use took the same time as the planner's bitmap scan plus sort (90 to 176 ms against 130 to
  270 ms at the node), and its estimated cost crosses PostgreSQL's JIT threshold, which adds hundreds of
  milliseconds on this machine. No change.
- Memory was the binding constraint of the machine throughout. Re-run on production-like hardware before
  comparing absolute numbers.
