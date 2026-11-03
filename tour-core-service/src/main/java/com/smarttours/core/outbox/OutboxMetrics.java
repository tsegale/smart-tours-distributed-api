package com.smarttours.core.outbox;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * Relay lag, read from the table at scrape time (both gauges use the partial index on unpublished rows):
 * <ul>
 *   <li>{@code outbox_oldest_unpublished_age_seconds}: how long the oldest unpublished event has waited; 0 when
 *       the outbox is drained. The alert to have: this growing means events are not reaching consumers.</li>
 *   <li>{@code outbox_unpublished_events}: the backlog.</li>
 * </ul>
 * booking-service exports the same two gauges for its outbox, so one dashboard and one alert cover both.
 */
@Component
public class OutboxMetrics {

    private static final Logger log = LoggerFactory.getLogger(OutboxMetrics.class);

    private final JdbcClient jdbc;

    public OutboxMetrics(JdbcClient jdbc, MeterRegistry meters) {
        this.jdbc = jdbc;
        Gauge.builder("outbox.oldest.unpublished.age", this, OutboxMetrics::oldestUnpublishedAgeSeconds)
                .baseUnit("seconds")
                .description("Age of the oldest event not yet confirmed by the broker; 0 when there is none")
                .register(meters);
        Gauge.builder("outbox.unpublished", this, OutboxMetrics::unpublished)
                .baseUnit("events")
                .description("Events written but not yet confirmed by the broker")
                .register(meters);
    }

    double oldestUnpublishedAgeSeconds() {
        return query("""
                SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(occurred_at)), 0)
                FROM outbox WHERE published_at IS NULL
                """);
    }

    double unpublished() {
        return query("SELECT count(*) FROM outbox WHERE published_at IS NULL");
    }

    /** NaN, not 0, when the database can't be read: a scrape must not report a healthy outbox it hasn't seen. */
    private double query(String sql) {
        try {
            return jdbc.sql(sql).query(Double.class).single();
        } catch (DataAccessException ex) {
            log.warn("Outbox metrics unavailable: {}", ex.getMessage());
            return Double.NaN;
        }
    }
}
