-- Daily reference exchange rates for display prices (ADR 0011). Prices stay in NAD; these rates only
-- convert them for display. Every accepted fetch is kept as history: one row per pair, publication date
-- and source, overwritten if the same date is fetched again later in the day.
CREATE TABLE fx_rates (
    id         BIGSERIAL     PRIMARY KEY,
    base       CHAR(3)       NOT NULL CHECK (base ~ '^[A-Z]{3}$'),
    quote      CHAR(3)       NOT NULL CHECK (quote ~ '^[A-Z]{3}$'),
    -- 1 base = rate quote. Eight decimals: NAD/EUR is about 0.054, and Frankfurter publishes up to five
    -- significant digits, so nothing a provider sends is ever rounded.
    rate       NUMERIC(18,8) NOT NULL CHECK (rate > 0),
    as_of      DATE          NOT NULL,
    source     VARCHAR(64)   NOT NULL,
    fetched_at TIMESTAMPTZ   NOT NULL,
    CONSTRAINT chk_fx_rates_pair CHECK (base <> quote),
    -- Also serves the "newest per quote" lookup (WHERE base = ? ORDER BY quote, as_of DESC).
    CONSTRAINT uq_fx_rates_pair_day_source UNIQUE (base, quote, as_of, source)
);

-- ShedLock's lock table (JDBC provider): one row per scheduled task, so the daily rate refresh runs on one
-- instance at a time. Times are the database's own (usingDbTime), so instance clock skew can't break the lock.
CREATE TABLE shedlock (
    name       VARCHAR(64)  NOT NULL PRIMARY KEY,
    lock_until TIMESTAMP    NOT NULL,
    locked_at  TIMESTAMP    NOT NULL,
    locked_by  VARCHAR(255) NOT NULL
);
