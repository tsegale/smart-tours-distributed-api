package com.smarttours.core.tour.dto;

import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import com.smarttours.core.tour.Tour;

import java.math.BigDecimal;
import java.time.Instant;

/** A tour as clients see it. {@code avgRating} is null until the first verified review. */
public record TourResponse(
        Long id,
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
        Instant createdAt) {

    public static TourResponse from(Tour tour) {
        return new TourResponse(
                tour.getId(),
                tour.getName(),
                tour.getDescription(),
                tour.getPrice(),
                tour.getCurrency(),
                tour.getLatitude(),
                tour.getLongitude(),
                tour.getCategory(),
                tour.getDifficulty(),
                tour.getDurationMinutes(),
                tour.getRatingCount() == 0 ? null : tour.getAvgRating(),
                tour.getRatingCount(),
                tour.getCreatedAt());
    }
}
