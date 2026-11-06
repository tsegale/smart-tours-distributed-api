package com.smarttours.core.events;

import com.smarttours.core.outbox.Outbox;
import com.smarttours.core.tour.Tour;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * Published with routing key {@code tour.updated} through the outbox, in the transaction of an operator's PUT.
 * Same shape as
 * {@link TourCreatedEvent}, so consumers that index tours (the AI worker re-embeds on it) handle
 * both with one parser.
 */
public record TourUpdatedEvent(
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

    public static final String TYPE = "tour.updated";

    public static TourUpdatedEvent of(Tour tour) {
        return new TourUpdatedEvent(UUID.randomUUID(), TYPE, Instant.now(),
                tour.getId(), tour.getName(), tour.getDescription(), tour.getMeetingPoint(), tour.getTimeZone(),
                tour.getVersion(), tour.getLocation().getY(), tour.getLocation().getX(), tour.getPrice(),
                tour.getCurrency(), tour.getCategory().name(), tour.getDifficulty().name(), tour.getDurationMinutes());
    }
}
