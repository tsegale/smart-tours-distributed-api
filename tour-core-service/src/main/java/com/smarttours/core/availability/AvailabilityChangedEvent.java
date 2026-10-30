package com.smarttours.core.availability;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * {@code availability.changed} from booking-service's outbox: the full state of one departure after
 * a seat change, with the departure's version. Mirrors {@code app.services.outbox.availability_changed}.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AvailabilityChangedEvent(
        @NotNull UUID eventId,
        @NotNull String eventType,
        OffsetDateTime occurredAt,
        @NotNull @Positive Long availabilityId,
        @NotNull @Positive Long tourId,
        @NotNull OffsetDateTime startsAt,
        @NotNull @Positive Integer totalSlots,
        @NotNull @PositiveOrZero Integer slotsLeft,
        @NotNull @PositiveOrZero Long version) {
}
