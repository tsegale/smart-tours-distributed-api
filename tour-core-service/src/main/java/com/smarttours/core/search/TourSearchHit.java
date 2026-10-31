package com.smarttours.core.search;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.smarttours.core.fx.CurrencyConversion;
import com.smarttours.core.fx.DisplayPrice;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * One search result. {@code avgRating} is null until the first verified review; {@code distanceMeters}
 * is present when the search had a point (spherical distance, to 0.1 m). {@code displayPrice} and
 * {@code displayPriceUnavailable} as on a tour (ADR 0011), added after the cache.
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
        Double distanceMeters,
        @JsonInclude(JsonInclude.Include.NON_NULL) DisplayPrice displayPrice,
        @JsonInclude(JsonInclude.Include.NON_NULL) String displayPriceUnavailable) {

    public TourSearchHit priced(CurrencyConversion conversion) {
        if (!conversion.changesRepresentation()) {
            return this;
        }
        return new TourSearchHit(id, name, description, price, currency, latitude, longitude, category, difficulty,
                durationMinutes, avgRating, ratingCount, createdAt, distanceMeters, conversion.display(price),
                conversion.unavailableReason());
    }
}
