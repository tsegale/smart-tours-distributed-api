-- Transactional outbox (ADR 0013): tour.created / tour.updated are written in the same transaction as the tour,
-- and the relay (OutboxRelay) publishes them to RabbitMQ afterwards. Same table as booking-service's outbox
-- (ADR 0008), so both services share one envelope and one relay design.
CREATE TABLE outbox (
    -- Also the event's eventId, so a consumer's dedupe key is fixed when the row is written.
    id             UUID         PRIMARY KEY,
    aggregate_type VARCHAR(50)  NOT NULL,
    aggregate_id   VARCHAR(64)  NOT NULL,
    -- The routing key on the smarttours.events exchange, e.g. tour.updated.
    event_type     VARCHAR(100) NOT NULL,
    payload        JSONB        NOT NULL,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    published_at   TIMESTAMPTZ
);

-- The relay's query: oldest unpublished first. Partial, so it only ever holds the backlog.
CREATE INDEX ix_outbox_unpublished ON outbox (occurred_at, id) WHERE published_at IS NULL;

-- The pruner's query: published rows past the retention window.
CREATE INDEX ix_outbox_published_at ON outbox (published_at) WHERE published_at IS NOT NULL;
