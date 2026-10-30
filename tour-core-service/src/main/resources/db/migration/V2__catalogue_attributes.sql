-- Search facets, ratings and a full-text vector for the catalogue.
--
-- Rows that predate this migration get placeholder facets (CULTURAL, EASY, 4 hours) so the new
-- columns can be NOT NULL. The defaults are dropped again: new tours must always say what they are.

ALTER TABLE tours
    ADD COLUMN category         VARCHAR(20) NOT NULL DEFAULT 'CULTURAL'
        CHECK (category IN ('SAFARI', 'DESERT', 'COASTAL', 'CULTURAL', 'ADVENTURE', 'CITY', 'WILDLIFE',
                            'PHOTOGRAPHY')),
    ADD COLUMN difficulty       VARCHAR(20) NOT NULL DEFAULT 'EASY'
        CHECK (difficulty IN ('EASY', 'MODERATE', 'CHALLENGING', 'EXTREME')),
    ADD COLUMN duration_minutes INTEGER     NOT NULL DEFAULT 240
        CHECK (duration_minutes > 0 AND duration_minutes <= 43200),
    -- Ratings come from verified reviews (review.submitted events from booking-service).
    -- The sum is kept instead of a running average, so the average never drifts through rounding:
    -- each review is one exact integer addition, and avg_rating is derived from sum and count.
    ADD COLUMN rating_sum       BIGINT      NOT NULL DEFAULT 0 CHECK (rating_sum >= 0),
    ADD COLUMN rating_count     INTEGER     NOT NULL DEFAULT 0 CHECK (rating_count >= 0),
    ADD CONSTRAINT ck_tours_rating_range CHECK (rating_sum BETWEEN rating_count AND 5 * rating_count);

ALTER TABLE tours
    ALTER COLUMN category DROP DEFAULT,
    ALTER COLUMN difficulty DROP DEFAULT,
    ALTER COLUMN duration_minutes DROP DEFAULT;

-- 0.00 means "no ratings yet" (rating_count = 0); the API reports that as null.
-- Stored, so it can be indexed and sorted on like any other column.
ALTER TABLE tours
    ADD COLUMN avg_rating NUMERIC(3, 2) NOT NULL
        GENERATED ALWAYS AS (CASE WHEN rating_count = 0 THEN 0
                                  ELSE round(rating_sum::numeric / rating_count, 2) END) STORED;

-- Name matches weigh more than description matches. The two-argument to_tsvector with a fixed
-- configuration is immutable, which a generated column requires.
ALTER TABLE tours
    ADD COLUMN search_vector tsvector NOT NULL
        GENERATED ALWAYS AS (setweight(to_tsvector('english', coalesce(name, '')), 'A')
                             || setweight(to_tsvector('english', coalesce(description, '')), 'B')) STORED;

CREATE INDEX idx_tours_search_vector ON tours USING GIN (search_vector);
