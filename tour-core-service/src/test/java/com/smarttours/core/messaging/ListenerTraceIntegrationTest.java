package com.smarttours.core.messaging;

import com.smarttours.core.availability.DepartureSummaryRepository;
import com.smarttours.core.config.RabbitMQConfig;
import com.smarttours.core.rating.TourRatingRepository;
import com.smarttours.core.support.IntegrationTest;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.tracing.test.autoconfigure.AutoConfigureTracing;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

/**
 * Tour-core's listeners continue the trace a message carries, through a real broker. The check runs inside the code the
 * listener calls: a span existing somewhere isn't enough, the work itself has to be in the publisher's trace.
 */
@AutoConfigureTracing
@TestPropertySource(properties = {
        // As with the observability profile. Spans to an endpoint nobody listens on are dropped.
        "management.tracing.export.enabled=true",
        "management.tracing.sampling.probability=1.0"
})
class ListenerTraceIntegrationTest extends IntegrationTest {

    @Autowired
    private Tracer tracer;

    @MockitoSpyBean
    private DepartureSummaryRepository summaries;

    @MockitoSpyBean
    private TourRatingRepository ratings;

    private static Message message(String routingKey, String body) {
        return MessageBuilder.withBody(body.getBytes(StandardCharsets.UTF_8))
                .setContentType(MessageProperties.CONTENT_TYPE_JSON)
                .setMessageId(UUID.randomUUID().toString())
                .setType(routingKey)
                .build();
    }

    /** Publishes from inside a span, as the outbox relay does: the template puts its traceparent in the headers. */
    private String publishInASpan(String routingKey, String body) {
        Span span = tracer.nextSpan().name("outbox.relay " + routingKey).start();
        try (Tracer.SpanInScope ignored = tracer.withSpan(span)) {
            rabbitTemplate.send(RabbitMQConfig.EVENTS_EXCHANGE, routingKey, message(routingKey, body));
            return span.context().traceId();
        } finally {
            span.end();
        }
    }

    private static String availabilityChanged() {
        return """
                {"eventId":"%s","eventType":"availability.changed","occurredAt":"%s","availabilityId":%d,"tourId":1,
                 "startsAt":"2030-12-01T06:00:00Z","totalSlots":10,"slotsLeft":9,"version":1}
                """.formatted(UUID.randomUUID(), Instant.now(), System.nanoTime() % 1_000_000_000L);
    }

    private static String reviewSubmitted() {
        return """
                {"eventId":"%s","eventType":"review.submitted","occurredAt":"%s","reviewId":1,"bookingId":1,"tourId":1,
                 "rating":5}
                """.formatted(UUID.randomUUID(), Instant.now());
    }

    private String currentTraceId() {
        Span span = tracer.currentSpan();
        return span == null ? null : span.context().traceId();
    }

    @Test
    void theAvailabilityListenerContinuesTheTrace() {
        AtomicReference<String> seen = new AtomicReference<>();
        Mockito.doAnswer(call -> {
            seen.set(currentTraceId());
            return call.callRealMethod();
        }).when(summaries).applyIfNewer(Mockito.any());

        String traceId = publishInASpan("availability.changed", availabilityChanged());

        assertThat(traceId).hasSize(32);
        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> assertThat(seen.get()).isEqualTo(traceId));
    }

    @Test
    void theRatingListenerContinuesTheTrace() {
        AtomicReference<String> seen = new AtomicReference<>();
        Mockito.doAnswer(call -> {
            seen.set(currentTraceId());
            return call.callRealMethod();
        }).when(ratings).addRatingOnce(Mockito.any(), Mockito.anyLong(), Mockito.anyInt());

        String traceId = publishInASpan("review.submitted", reviewSubmitted());

        assertThat(traceId).hasSize(32);
        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> assertThat(seen.get()).isEqualTo(traceId));
    }

    @Test
    void separateMessagesAreSeparateTraces() {
        AtomicReference<String> seen = new AtomicReference<>();
        Mockito.doAnswer(call -> {
            seen.set(currentTraceId());
            return call.callRealMethod();
        }).when(summaries).applyIfNewer(Mockito.any());

        String first = publishInASpan("availability.changed", availabilityChanged());
        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> assertThat(seen.get()).isEqualTo(first));
        String second = publishInASpan("availability.changed", availabilityChanged());
        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> assertThat(seen.get()).isEqualTo(second));

        assertThat(second).isNotEqualTo(first);
    }
}
