package com.smarttours.core.tour.dto;

import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;

/** Body of POST /api/tours and PUT /api/tours/{id}: every operator-editable attribute. */
public record TourRequest(
        @NotBlank @Size(max = 200) String name,
        @Size(max = 5000) String description,
        @NotNull @DecimalMin("0.00") @Digits(integer = 8, fraction = 2) BigDecimal price,
        @NotNull @DecimalMin("-90.0") @DecimalMax("90.0") Double latitude,
        @NotNull @DecimalMin("-180.0") @DecimalMax("180.0") Double longitude,
        @NotNull Category category,
        @NotNull Difficulty difficulty,
        // Up to 30 days, matching the CHECK constraint on tours.duration_minutes.
        @NotNull @Positive @Max(43_200) Integer durationMinutes) {
}
