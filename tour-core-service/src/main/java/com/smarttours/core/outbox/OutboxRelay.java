package com.smarttours.core.outbox;

import com.smarttours.core.config.RabbitMQConfig;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Moves committed outbox rows to RabbitMQ: at-least-once, never for a change that rolled back (ADR 0013).
 *
 * <p>One batch, in one transaction:
 * <ol>
 *   <li>{@code SELECT} the oldest unpublished rows {@code FOR UPDATE SKIP LOCKED}, {@code LIMIT batch};</li>
 *   <li>publish each with a {@link CorrelationData} and wait for every publisher confirm;</li>
 *   <li>only if all were acked, set {@code published_at} on exactly those rows, and commit.</li>
 * </ol>
 *
 * <ul>
 *   <li>Several instances can relay at once without a leader: a second relay skips the rows the first has
 *       locked and takes the next ones, so no batch is published twice.</li>
 *   <li>A nack, a timeout or a broker outage throws before step 3: the transaction rolls back, nothing is marked
 *       and the batch is retried on the next run.</li>
 *   <li>A crash between steps 2 and 3 also rolls back, so the rows are published again: a duplicate, never a
 *       loss. Consumers dedupe by {@code eventId}.</li>
 * </ul>
 *
 * The same design, envelope and message properties as booking-service's relay (ADR 0008).
 */
@Component
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final JdbcClient jdbc;
    private final RabbitTemplate rabbit;
    private final TransactionTemplate transactions;
    private final OutboxProperties properties;
    private final TraceParents traces;
    private final Counter published;
    private final Counter failures;

    public OutboxRelay(JdbcClient jdbc, RabbitTemplate rabbit, TransactionTemplate transactions,
                       OutboxProperties properties, TraceParents traces, MeterRegistry meters) {
        this.jdbc = jdbc;
        this.rabbit = rabbit;
        this.transactions = transactions;
        this.properties = properties;
        this.traces = traces;
        this.published = Counter.builder("outbox.published").description("Events confirmed by the broker")
                .register(meters);
        this.failures = Counter.builder("outbox.relay.failures").description("Batches not confirmed, retried later")
                .register(meters);
    }

    /** Relays until a batch comes back short, then waits for the next poll. */
    @Scheduled(fixedDelayString = "${smarttours.outbox.poll-interval}",
            initialDelayString = "${smarttours.outbox.poll-interval}")
    public void relay() {
        try {
            while (relayBatch() == properties.batchSize()) {
                log.debug("Full outbox batch relayed, continuing");
            }
        } catch (RuntimeException ex) {
            failures.increment();
            log.warn("Outbox batch not published, retrying on the next poll: {}", ex.getMessage());
        }
    }

    /** Publishes one batch. Returns how many rows were published (0 when there was nothing to do). */
    public int relayBatch() {
        Integer count = transactions.execute(status -> {
            List<Row> rows = jdbc.sql("""
                            SELECT id, event_type, payload::text AS payload, occurred_at, traceparent
                            FROM outbox
                            WHERE published_at IS NULL
                            ORDER BY occurred_at, id
                            LIMIT :limit
                            FOR UPDATE SKIP LOCKED
                            """)
                    .param("limit", properties.batchSize())
                    .query((rs, n) -> new Row(rs.getObject("id", UUID.class), rs.getString("event_type"),
                            rs.getString("payload"), rs.getTimestamp("occurred_at").toInstant(),
                            rs.getString("traceparent")))
                    .list();
            if (rows.isEmpty()) {
                return 0;
            }
            publishConfirmed(rows);
            jdbc.sql("UPDATE outbox SET published_at = now() WHERE id IN (:ids)")
                    .param("ids", rows.stream().map(Row::id).toList())
                    .update();
            return rows.size();
        });
        int relayed = count == null ? 0 : count;
        published.increment(relayed);
        return relayed;
    }

    /** Deletes published rows past the retention window, in batches so no single statement runs long. */
    @Scheduled(fixedDelayString = "${smarttours.outbox.prune-interval}",
            initialDelayString = "${smarttours.outbox.prune-interval}")
    public void prune() {
        Timestamp cutoff = Timestamp.from(Instant.now().minus(properties.retention()));
        int deleted = 0;
        int batch;
        do {
            batch = jdbc.sql("""
                            DELETE FROM outbox WHERE id IN (
                                SELECT id FROM outbox WHERE published_at < :cutoff LIMIT :limit)
                            """)
                    .param("cutoff", cutoff)
                    .param("limit", properties.pruneBatchSize())
                    .update();
            deleted += batch;
        } while (batch == properties.pruneBatchSize());
        if (deleted > 0) {
            log.info("Pruned {} published outbox rows", deleted);
        }
    }

    private void publishConfirmed(List<Row> rows) {
        List<CorrelationData> pending = new ArrayList<>(rows.size());
        for (Row row : rows) {
            CorrelationData correlation = new CorrelationData(row.id().toString());
            sendInItsTrace(row, correlation);
            pending.add(correlation);
        }
        long deadline = System.nanoTime() + properties.confirmTimeout().toNanos();
        for (CorrelationData correlation : pending) {
            CorrelationData.Confirm confirm = await(correlation, deadline);
            if (!confirm.ack()) {
                throw new OutboxPublishException("broker nacked event " + correlation.getId() + ": "
                        + confirm.reason());
            }
        }
    }

    /** Inside the trace of the transaction that wrote the row, so the consumer continues that trace (ADR 0017). */
    private void sendInItsTrace(Row row, CorrelationData correlation) {
        Span span = traces.continueFrom(row.traceparent(), "outbox.relay " + row.eventType());
        if (span == null) {
            rabbit.send(RabbitMQConfig.EVENTS_EXCHANGE, row.eventType(), message(row), correlation);
            return;
        }
        try (Tracer.SpanInScope ignored = traces.inScope(span)) {
            rabbit.send(RabbitMQConfig.EVENTS_EXCHANGE, row.eventType(), message(row), correlation);
        } finally {
            span.end();
        }
    }

    private static CorrelationData.Confirm await(CorrelationData correlation, long deadline) {
        try {
            return correlation.getFuture().get(Math.max(0, deadline - System.nanoTime()), TimeUnit.NANOSECONDS);
        } catch (TimeoutException ex) {
            throw new OutboxPublishException("no confirm for event " + correlation.getId() + " in time", ex);
        } catch (ExecutionException ex) {
            throw new OutboxPublishException("confirm failed for event " + correlation.getId(), ex.getCause());
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new OutboxPublishException("interrupted waiting for confirms", ex);
        }
    }

    private static Message message(Row row) {
        MessageBuilder builder = MessageBuilder.withBody(row.payload().getBytes(StandardCharsets.UTF_8));
        if (row.traceparent() != null) {
            // The template's observation replaces this with its own span; it stays if observation is off.
            builder.setHeader(TraceParents.HEADER, row.traceparent());
        }
        return builder
                .setContentType(MessageProperties.CONTENT_TYPE_JSON)
                .setContentEncoding(StandardCharsets.UTF_8.name())
                .setDeliveryMode(MessageDeliveryMode.PERSISTENT)
                .setMessageId(row.id().toString())
                .setType(row.eventType())
                .setTimestamp(Date.from(row.occurredAt()))
                .build();
    }

    private record Row(UUID id, String eventType, String payload, Instant occurredAt, String traceparent) {
    }

    /** A batch the broker didn't confirm; nothing in it is marked published. */
    public static class OutboxPublishException extends RuntimeException {

        public OutboxPublishException(String message) {
            super(message);
        }

        public OutboxPublishException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
