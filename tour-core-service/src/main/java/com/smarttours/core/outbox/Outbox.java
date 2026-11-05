package com.smarttours.core.outbox;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

/**
 * Writes integration events to the transactional outbox (ADR 0013). Must run inside the transaction that makes
 * the change, so the event commits or rolls back with it; nothing here talks to RabbitMQ ({@link OutboxRelay}
 * does that afterwards).
 *
 * <p>The payload is the event record as JSON: {@code eventId}, {@code eventType}, {@code occurredAt} and the
 * event's own fields, the envelope booking-service's outbox uses too (docs/events.md).
 */
@Component
public class Outbox {

    private final JdbcClient jdbc;
    private final JsonMapper json;
    private final TraceParents traces;

    public Outbox(JdbcClient jdbc, JsonMapper json, TraceParents traces) {
        this.jdbc = jdbc;
        this.json = json;
        this.traces = traces;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void record(String aggregateType, Object aggregateId, OutboxEvent event) {
        jdbc.sql("""
                        INSERT INTO outbox (id, aggregate_type, aggregate_id, event_type, payload, occurred_at,
                                            traceparent)
                        VALUES (:id, :aggregateType, :aggregateId, :eventType, CAST(:payload AS jsonb), :occurredAt,
                                :traceparent)
                        """)
                .param("id", event.eventId())
                .param("aggregateType", aggregateType)
                .param("aggregateId", String.valueOf(aggregateId))
                .param("eventType", event.eventType())
                .param("payload", json.writeValueAsString(event))
                .param("occurredAt", Timestamp.from(event.occurredAt()))
                .param("traceparent", traces.current())
                .update();
    }

    /** What every event written to the outbox carries; the rest of the record is the event's own data. */
    public interface OutboxEvent {

        UUID eventId();

        String eventType();

        Instant occurredAt();
    }
}
