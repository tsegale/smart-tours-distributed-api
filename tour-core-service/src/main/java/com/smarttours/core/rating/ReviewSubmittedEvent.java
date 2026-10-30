package com.smarttours.core.rating;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

import java.time.OffsetDateTime;
import java.util.UUID;

/** {@code review.submitted} from booking-service: a verified rating for a tour. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ReviewSubmittedEvent(
        @NotNull UUID eventId,
        @NotNull String eventType,
        OffsetDateTime occurredAt,
        @NotNull @Positive Long reviewId,
        @NotNull @Positive Long bookingId,
        @NotNull @Positive Long tourId,
        @NotNull @Min(1) @Max(5) Integer rating) {
}
