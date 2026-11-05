package com.smarttours.core.outbox;

import com.smarttours.core.config.RabbitMQConfig;
import com.smarttours.core.support.IntegrationTest;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.tracing.test.autoconfigure.AutoConfigureTracing;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Instant;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/** A trace survives the outbox, through a real broker: the consumer sees the trace the transaction started. */
@AutoConfigureTracing
@TestPropertySource(properties = {
        // As with the observability profile. Spans to an endpoint nobody listens on are dropped.
        "management.tracing.export.enabled=true",
        "management.tracing.sampling.probability=1.0"
})
class OutboxTraceIntegrationTest extends IntegrationTest {

    private static final String ROUTING_KEY = "trace.test";

    @Autowired
    private Outbox outbox;

    @Autowired
    private OutboxRelay relay;

    @Autowired
    private Tracer tracer;

    @Autowired
    private TransactionTemplate transactions;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private AmqpAdmin amqpAdmin;

    private Queue queue;

    private record TestEvent(UUID eventId, String eventType, Instant occurredAt) implements Outbox.OutboxEvent {
    }

    @BeforeEach
    void emptyOutboxAndListen() {
        jdbc.sql("DELETE FROM outbox").update();
        queue = new Queue("trace-test-" + UUID.randomUUID(), false, false, false);
        amqpAdmin.declareQueue(queue);
        amqpAdmin.declareBinding(BindingBuilder.bind(queue)
                .to(new TopicExchange(RabbitMQConfig.EVENTS_EXCHANGE)).with(ROUTING_KEY));
    }

    @AfterEach
    void stopListening() {
        amqpAdmin.deleteQueue(queue.getName());
    }

    private UUID record() {
        UUID eventId = UUID.randomUUID();
        transactions.executeWithoutResult(status ->
                outbox.record("test", 1, new TestEvent(eventId, ROUTING_KEY, Instant.now())));
        return eventId;
    }

    private String storedTraceparent(UUID eventId) {
        return jdbc.sql("SELECT traceparent FROM outbox WHERE id = :id").param("id", eventId)
                .query(String.class).optional().orElse(null);
    }

    @Test
    void theTraceIdSurvivesTheHop() {
        Span span = tracer.nextSpan().name("POST /api/tours").start();
        String traceId = span.context().traceId();
        assertThat(traceId).as("a real tracer, not the no-op one").hasSize(32);
        UUID eventId;
        try (Tracer.SpanInScope ignored = tracer.withSpan(span)) {
            eventId = record();
        } finally {
            span.end();
        }
        assertThat(storedTraceparent(eventId)).as("written with the row").contains(traceId);

        relay.relay();

        Message message = rabbitTemplate.receive(queue.getName(), 5000);
        assertThat(message).isNotNull();
        String header = message.getMessageProperties().getHeader(TraceParents.HEADER);
        assertThat(header).as("in the message the consumer gets").contains(traceId);
    }

    @Test
    void anEventWrittenOutsideATraceIsStillPublished() {
        UUID eventId = record();
        assertThat(storedTraceparent(eventId)).isNull();

        relay.relay();

        Message message = rabbitTemplate.receive(queue.getName(), 5000);
        assertThat(message).isNotNull();
        assertThat(message.getMessageProperties().getMessageId()).isEqualTo(eventId.toString());
    }
}
