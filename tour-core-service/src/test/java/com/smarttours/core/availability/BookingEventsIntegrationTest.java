package com.smarttours.core.availability;

import com.smarttours.core.messaging.ConsumerTopology;
import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import com.smarttours.core.tour.Tour;
import com.smarttours.core.tour.TourRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.amqp.core.AmqpAdmin;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.Optional;
import java.util.Properties;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

/**
 * booking-service events through a real RabbitMQ into tour-core's tables: the departure read model
 * and ratings. Messages are published as the Python outbox relay publishes them (raw JSON bytes).
 */
class BookingEventsIntegrationTest extends IntegrationTest {

    private static final GeometryFactory GEOMETRY = new GeometryFactory(new PrecisionModel(), 4326);
    private static final Duration WAIT = Duration.ofSeconds(15);

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private AmqpAdmin amqpAdmin;

    @Autowired
    private TourRepository tours;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE tour_departure_summary, processed_events").update();
    }

    @Test
    void availabilityChanged_insertsTheDeparture() {
        publish("availability.changed", availability(101, 7, 3, 10, 6));

        await().atMost(WAIT).until(() -> summary(101).isPresent());
        assertThat(summary(101)).contains(Map.of("tour_id", 7L, "slots_left", 6, "total_slots", 10,
                "source_version", 3L));
    }

    @Test
    void newerVersions_overwrite_olderAndDuplicateOnesAreIgnored() {
        publish("availability.changed", availability(102, 7, 1, 10, 10));
        publish("availability.changed", availability(102, 7, 4, 10, 2));
        // Overtaken and duplicate deliveries, as several outbox relays and redelivery can produce.
        publish("availability.changed", availability(102, 7, 2, 10, 8));
        publish("availability.changed", availability(102, 7, 3, 10, 5));
        publish("availability.changed", availability(102, 7, 4, 10, 2));
        // A marker on another departure, published last: once it is applied, every earlier message has been.
        publish("availability.changed", availability(999, 7, 1, 10, 10));

        await().atMost(WAIT).until(() -> summary(999).isPresent());
        assertThat(summary(102).orElseThrow()).containsEntry("source_version", 4L).containsEntry("slots_left", 2);
    }

    @Test
    void outOfOrderDelivery_endsWithTheNewestState() {
        publish("availability.changed", availability(103, 7, 5, 10, 0));
        publish("availability.changed", availability(103, 7, 1, 10, 10));
        publish("availability.changed", availability(998, 7, 1, 10, 10));

        await().atMost(WAIT).until(() -> summary(998).isPresent());
        assertThat(summary(103).orElseThrow()).containsEntry("source_version", 5L).containsEntry("slots_left", 0);
    }

    @Test
    void poisonMessage_goesToTheDlqWithoutBlockingTheQueue() {
        String dlq = ConsumerTopology.deadLetterQueue(ConsumerTopology.AVAILABILITY_QUEUE);
        int before = depth(dlq);

        publishRaw("availability.changed", "{not json");
        publish("availability.changed", """
                {"eventId": "%s", "eventType": "availability.changed", "availabilityId": 104}
                """.formatted(UUID.randomUUID()));  // valid JSON, missing required fields
        publish("availability.changed", availability(105, 7, 1, 4, 4));

        await().atMost(WAIT).until(() -> depth(dlq) == before + 2);
        await().atMost(WAIT).until(() -> summary(105).isPresent());
        assertThat(summary(104)).isEmpty();
    }

    @Test
    void reviewSubmitted_addsTheRatingExactlyOnce() {
        long tourId = tours.save(new Tour("Etosha Game Drive", "Lions", new BigDecimal("2400.00"),
                GEOMETRY.createPoint(new Coordinate(16.33, -18.86)), Category.SAFARI, Difficulty.EASY, 480)).getId();
        long versionBefore = tours.findById(tourId).orElseThrow().getVersion();
        UUID first = UUID.randomUUID();

        publish("review.submitted", review(first, tourId, 5));
        publish("review.submitted", review(first, tourId, 5));  // redelivered
        publish("review.submitted", review(UUID.randomUUID(), tourId, 4));

        await().atMost(WAIT).until(() -> ratingCount(tourId) == 2);
        // Give a wrongly applied duplicate time to show up before asserting it didn't.
        await().pollDelay(Duration.ofMillis(500)).atMost(WAIT).until(() -> true);
        Tour tour = tours.findById(tourId).orElseThrow();
        assertThat(tour.getRatingCount()).isEqualTo(2);
        assertThat(tour.getAvgRating()).isEqualByComparingTo("4.50");
        assertThat(tour.getVersion()).as("a rating changes the representation, so the ETag").isEqualTo(versionBefore + 2);
    }

    @Test
    void reviewForUnknownTour_isAcknowledgedNotRetried() {
        String dlq = ConsumerTopology.deadLetterQueue(ConsumerTopology.RATING_QUEUE);
        int before = depth(dlq);
        UUID eventId = UUID.randomUUID();

        publish("review.submitted", review(eventId, 987_654, 3));

        await().atMost(WAIT).until(() -> processed(eventId));
        assertThat(depth(dlq)).isEqualTo(before);
    }

    private void publish(String routingKey, String json) {
        publishRaw(routingKey, json);
    }

    private void publishRaw(String routingKey, String body) {
        MessageProperties properties = new MessageProperties();
        properties.setContentType(MessageProperties.CONTENT_TYPE_JSON);
        rabbitTemplate.send("smarttours.events", routingKey,
                MessageBuilder.withBody(body.getBytes(StandardCharsets.UTF_8)).andProperties(properties).build());
    }

    private static String availability(long availabilityId, long tourId, long version, int total, int left) {
        return """
                {"eventId": "%s", "eventType": "availability.changed", "occurredAt": "2026-10-30T10:00:00.123456+00:00",
                 "availabilityId": %d, "tourId": %d, "startsAt": "2030-12-01T04:00:00+00:00",
                 "totalSlots": %d, "slotsLeft": %d, "version": %d}
                """.formatted(UUID.randomUUID(), availabilityId, tourId, total, left, version);
    }

    private static String review(UUID eventId, long tourId, int rating) {
        return """
                {"eventId": "%s", "eventType": "review.submitted", "occurredAt": "2026-10-30T10:00:00+00:00",
                 "reviewId": 1, "bookingId": 1, "tourId": %d, "rating": %d}
                """.formatted(eventId, tourId, rating);
    }

    private Optional<Map<String, Object>> summary(long availabilityId) {
        return jdbc.sql("""
                        SELECT tour_id, slots_left, total_slots, source_version
                        FROM tour_departure_summary WHERE availability_id = ?""")
                .param(availabilityId).query().listOfRows().stream().findFirst();
    }

    private int ratingCount(long tourId) {
        return jdbc.sql("SELECT rating_count FROM tours WHERE id = ?").param(tourId).query(Integer.class).single();
    }

    private boolean processed(UUID eventId) {
        return jdbc.sql("SELECT count(*) FROM processed_events WHERE event_id = ?").param(eventId)
                .query(Integer.class).single() == 1;
    }

    private int depth(String queue) {
        Properties info = amqpAdmin.getQueueProperties(queue);
        return info == null ? 0 : ((Number) info.get("QUEUE_MESSAGE_COUNT")).intValue();
    }
}
