-- Read model of booking-service's departures, fed by availability.changed events (CQRS, ADR 0008).
-- booking-service stays the source of truth: this copy only answers "does this tour have a departure
-- in that date range with enough seats left?" for search, and may lag by a few seconds.
--
-- No foreign key to tours: a projection must accept whatever the source publishes, in any order.
CREATE TABLE tour_departure_summary (
    availability_id BIGINT      PRIMARY KEY,
    tour_id         BIGINT      NOT NULL,
    starts_at       TIMESTAMPTZ NOT NULL,
    total_slots     INTEGER     NOT NULL CHECK (total_slots > 0),
    slots_left      INTEGER     NOT NULL CHECK (slots_left >= 0 AND slots_left <= total_slots),
    -- tour_availability.version in booking-service. An event is applied only if it is newer.
    source_version  BIGINT      NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Search asks, per candidate tour: any departure of this tour in [from, to) with slots_left >= n?
CREATE INDEX idx_departure_summary_tour_starts ON tour_departure_summary (tour_id, starts_at) INCLUDE (slots_left);

-- Event ids already applied, for consumers whose effect is not naturally idempotent
-- (adding a rating twice would count it twice). Written in the same transaction as the effect.
CREATE TABLE processed_events (
    event_id     UUID         PRIMARY KEY,
    event_type   VARCHAR(100) NOT NULL,
    processed_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
