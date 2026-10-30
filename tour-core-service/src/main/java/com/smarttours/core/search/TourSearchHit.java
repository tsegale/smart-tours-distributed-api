package com.smarttours.core.search;

import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * One search result. {@code avgRating} is null until the first verified review; {@code distanceMeters}
 * is present when the search had a point (spherical distance, to 0.1 m).
 */
public record TourSearchHit(
        long id,
        String name,
        String description,
        BigDecimal price,
        String currency,
        double latitude,
        double longitude,
        Category category,
        Difficulty difficulty,
        int durationMinutes,
        BigDecimal avgRating,
        int ratingCount,
        Instant createdAt,
        Double distanceMeters) {
}
