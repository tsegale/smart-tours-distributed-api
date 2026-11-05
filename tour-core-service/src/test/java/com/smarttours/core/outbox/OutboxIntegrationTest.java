package com.smarttours.core.outbox;

import com.smarttours.core.config.RabbitMQConfig;
import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.support.SharedContainers;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import com.smarttours.core.tour.TourService;
import com.smarttours.core.tour.dto.TourRequest;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The tour-core outbox against real Postgres and RabbitMQ: written in the tour's transaction, relayed with
 * publisher confirms, at-least-once and never for a rolled-back change (ADR 0013).
 */
class OutboxIntegrationTest extends IntegrationTest {

    private static final TourRequest REQUEST = new TourRequest("Etosha Game Drive", "Big five at the waterholes",
            new BigDecimal("2400.00"), -18.8556, 16.3294, Category.SAFARI, Difficulty.EASY, 480, null, null);

    @Autowired
    private TourService tours;

    @Autowired
    private Outbox outbox;

    @Autowired
    private OutboxRelay relay;

    @Autowired
    private OutboxMetrics metrics;

    @Autowired
    private OutboxProperties properties;

    @Autowired
    private TraceParents traces;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private TransactionTemplate transactions;

    @Autowired
    private AmqpAdmin admin;

    @Autowired
    private JsonMapper json;

    private Queue queue;

    @BeforeEach
    void emptyOutboxAndListen() {
        jdbc.sql("DELETE FROM outbox").update();
        // Not auto-delete: receive() consumes and cancels, which would delete an auto-delete queue.
        queue = new Queue("outbox-test-" + UUID.randomUUID(), false, false, false);
        admin.declareQueue(queue);
        admin.declareBinding(BindingBuilder.bind(queue)
                .to(new TopicExchange(RabbitMQConfig.EVENTS_EXCHANGE)).with("tour.#"));
    }

    @AfterEach
    void stopListening() {
        admin.deleteQueue(queue.getName());
    }

    @Test
    void aRolledBackChangeLeavesNoEventAndNothingIsWrittenOutsideATransaction() {
        transactions.executeWithoutResult(status -> {
            tours.create(REQUEST);
            status.setRollbackOnly();
        });

        assertThat(pending()).isZero();
        assertThat(jdbc.sql("SELECT count(*) FROM outbox").query(Long.class).single()).isZero();
        TourCreatedEvent event = new TourCreatedEvent(UUID.randomUUID(), TourCreatedEvent.TYPE, Instant.now(), 1L,
                "n", "d", null, "Africa/Windhoek");
        assertThatThrownBy(() -> outbox.record("tour", 1L, event)).isInstanceOf(IllegalTransactionStateException.class);
    }

    @Test
    void aCommittedChangeIsPublishedOnceWithTheSharedEnvelope() throws Exception {
        long tourId = tours.create(REQUEST).tour().id();
        UUID eventId = jdbc.sql("SELECT id FROM outbox").query(UUID.class).single();

        relay.relay();

        Message message = rabbitTemplate.receive(queue.getName(), 5000);
        assertThat(message).isNotNull();
        assertThat(message.getMessageProperties().getMessageId()).isEqualTo(eventId.toString());
        assertThat(message.getMessageProperties().getType()).isEqualTo("tour.created");
        assertThat(message.getMessageProperties().getReceivedRoutingKey()).isEqualTo("tour.created");
        assertThat(message.getMessageProperties().getContentType()).isEqualTo("application/json");
        assertThat(message.getMessageProperties().getReceivedDeliveryMode()).isEqualTo(MessageDeliveryMode.PERSISTENT);
        JsonNode body = json.readTree(message.getBody());
        assertThat(body.get("eventId").asString()).isEqualTo(eventId.toString());
        assertThat(body.get("eventType").asString()).isEqualTo("tour.created");
        assertThat(body.get("occurredAt").asString()).isNotBlank();
        assertThat(body.get("tourId").asLong()).isEqualTo(tourId);
        assertThat(body.get("name").asString()).isEqualTo("Etosha Game Drive");

        assertThat(pending()).isZero();
        relay.relay();
        assertThat(rabbitTemplate.receive(queue.getName(), 500)).as("published once").isNull();
    }

    @Test
    void whileTheBrokerIsUnreachableEventsWaitAndArePublishedAfterRecovery() throws Exception {
        tours.create(REQUEST);
        tours.create(REQUEST);
        var docker = SharedContainers.RABBITMQ.getDockerClient();
        String container = SharedContainers.RABBITMQ.getContainerId();

        docker.pauseContainerCmd(container).exec();
        try {
            assertThatThrownBy(() -> relay.relayBatch()).isInstanceOf(RuntimeException.class);
            assertThat(pending()).as("nothing marked without a confirm").isEqualTo(2);
            assertThat(metrics.oldestUnpublishedAgeSeconds()).isPositive();
        } finally {
            docker.unpauseContainerCmd(container).exec();
        }

        int published = 0;
        for (int attempt = 0; attempt < 20 && published == 0; attempt++) {
            try {
                published = relay.relayBatch();
            } catch (RuntimeException brokerStillRecovering) {
                Thread.sleep(500);
            }
        }
        assertThat(published).isEqualTo(2);
        assertThat(pending()).isZero();
        assertThat(metrics.oldestUnpublishedAgeSeconds()).isZero();
        Set<String> received = drain();
        assertThat(received).as("each event at least once (a duplicate of the unconfirmed batch is allowed)")
                .containsAll(jdbc.sql("SELECT id::text FROM outbox").query(String.class).list());
    }

    @Test
    void twoRelaysNeverPublishTheSameEventTwice() throws Exception {
        int events = 2 * properties.batchSize() + 50;
        transactions.executeWithoutResult(status -> {
            for (int i = 0; i < events; i++) {
                TourCreatedEvent event = new TourCreatedEvent(UUID.randomUUID(), TourCreatedEvent.TYPE, Instant.now(),
                        i, "Tour " + i, "d", null, "Africa/Windhoek");
                outbox.record("tour", i, event);
            }
        });
        MeterRegistry meters = new SimpleMeterRegistry();
        OutboxRelay second = new OutboxRelay(jdbc, rabbitTemplate, transactions, properties, traces, meters);
        Callable<Integer> drainWith1 = () -> drainWith(relay);
        Callable<Integer> drainWith2 = () -> drainWith(second);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        List<Future<Integer>> results;
        try {
            results = pool.invokeAll(List.of(drainWith1, drainWith2));
        } finally {
            pool.shutdown();
        }

        assertThat(results.get(0).get() + results.get(1).get()).isEqualTo(events);
        assertThat(pending()).isZero();
        List<String> received = new ArrayList<>();
        Message message;
        while ((message = rabbitTemplate.receive(queue.getName(), 2000)) != null) {
            received.add(message.getMessageProperties().getMessageId());
        }
        assertThat(received).hasSize(events).doesNotHaveDuplicates();
    }

    @Test
    void publishedRowsArePrunedAfterTheRetentionAndUnpublishedOnesKept() {
        tours.create(REQUEST);
        tours.create(REQUEST);
        relay.relay();
        tours.create(REQUEST);
        jdbc.sql("UPDATE outbox SET published_at = now() - (:days || ' days')::interval WHERE published_at IS NOT NULL")
                .param("days", String.valueOf(properties.retention().toDays() + 1)).update();

        relay.prune();

        assertThat(jdbc.sql("SELECT count(*) FROM outbox").query(Long.class).single()).isEqualTo(1);
        assertThat(pending()).isEqualTo(1);
    }

    private int drainWith(OutboxRelay relay) {
        int total = 0;
        int batch;
        do {
            batch = relay.relayBatch();
            total += batch;
        } while (batch > 0);
        return total;
    }

    private long pending() {
        return jdbc.sql("SELECT count(*) FROM outbox WHERE published_at IS NULL").query(Long.class).single();
    }

    private Set<String> drain() {
        Set<String> ids = new HashSet<>();
        Message message;
        while ((message = rabbitTemplate.receive(queue.getName(), 2000)) != null) {
            ids.add(message.getMessageProperties().getMessageId());
        }
        return ids;
    }
}
