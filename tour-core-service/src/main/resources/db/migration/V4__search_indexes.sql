-- Indexes for GET /api/tours/search, each one kept because EXPLAIN (ANALYZE, BUFFERS) on the 100k seed showed
-- it earning its place (docs/benchmarks/search.md). Measured and rejected: a partial index on open departures
-- (WHERE slots_left > 0), which did no better than V3's (tour_id, starts_at) INCLUDE (slots_left).

-- Bounding-box filters (location && envelope). The geography GiST from V1 serves radius and distance, but its
-- expression can't answer a geometry && test. In a sparse map viewport sorted by price, the price index below
-- alone walks ~20k pages to find 21 rows; with this one the box is resolved first (148 pages, 0.3 ms).
CREATE INDEX idx_tours_location_geom ON tours USING GIST (location);

-- Sorts with their tie-breaker, so a page (first or deep, by keyset) is an index range scan that stops after
-- size + 1 rows instead of a sort of every match. Descending orders scan the same indexes backwards.
CREATE INDEX idx_tours_price_id ON tours (price, id);
CREATE INDEX idx_tours_rating_id ON tours (avg_rating, id);
CREATE INDEX idx_tours_created_id ON tours (created_at, id);
