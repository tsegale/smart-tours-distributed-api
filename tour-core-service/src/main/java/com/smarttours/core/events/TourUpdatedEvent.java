package com.smarttours.core.events;

import com.smarttours.core.outbox.Outbox;
import com.smarttours.core.tour.Tour;

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
        String timeZone) implements Outbox.OutboxEvent {

    public static final String TYPE = "tour.updated";

    public static TourUpdatedEvent of(Tour tour) {
        return new TourUpdatedEvent(UUID.randomUUID(), TYPE, Instant.now(),
                tour.getId(), tour.getName(), tour.getDescription(), tour.getMeetingPoint(), tour.getTimeZone());
    }
}
