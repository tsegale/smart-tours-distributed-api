-- The W3C trace context of the transaction that wrote each row, so its trace continues through the relay (ADR 0017).
ALTER TABLE outbox ADD COLUMN traceparent VARCHAR(55);
