package com.smarttours.core.events;

import com.smarttours.core.outbox.Outbox;
import com.smarttours.core.tour.Tour;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * Integration event published to the {@code smarttours.events} topic exchange with routing key
 * {@code tour.created}, through the transactional outbox (ADR 0013). The eventId lets consumers drop
 * duplicates: delivery is at-least-once, so every consumer must be idempotent. Schema: docs/events.md.
 */
public record TourCreatedEvent(
        UUID eventId,
        String eventType,
        Instant occurredAt,
        long tourId,
        String name,
        String description,
        String meetingPoint,
        String timeZone,
        long version,
        double latitude,
        double longitude,
        BigDecimal price,
        String currency,
        String category,
        String difficulty,
        int durationMinutes) implements Outbox.OutboxEvent {

    public static final String TYPE = "tour.created";

    public static TourCreatedEvent of(Tour tour) {
        return new TourCreatedEvent(UUID.randomUUID(), TYPE, Instant.now(),
                tour.getId(), tour.getName(), tour.getDescription(), tour.getMeetingPoint(), tour.getTimeZone(),
                tour.getVersion(), tour.getLocation().getY(), tour.getLocation().getX(), tour.getPrice(),
                tour.getCurrency(), tour.getCategory().name(), tour.getDifficulty().name(), tour.getDurationMinutes());
    }
}
