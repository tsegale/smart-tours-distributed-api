package com.smarttours.core.tour.dto;

import com.smarttours.core.tour.Tour;

import java.math.BigDecimal;
import java.time.Instant;

public record TourResponse(
        Long id,
        String name,
        String description,
        BigDecimal price,
        String currency,
        double latitude,
        double longitude,
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
                tour.getCreatedAt());
    }
}
