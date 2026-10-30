package com.smarttours.core.search;

import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import io.swagger.v3.oas.annotations.Parameter;
import org.springframework.format.annotation.DateTimeFormat;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * Query parameters of {@code GET /api/tours/search}, exactly as sent. Everything is optional; the
 * combinations are checked by {@link SearchCriteria#from}, which answers 400 for contradictions.
 * Multi-valued parameters accept repeats ({@code category=SAFARI&category=DESERT}) or commas.
 */
public record SearchParams(
        @Parameter(description = "Latitude of the centre point (WGS 84). Needs lon.") Double lat,
        @Parameter(description = "Longitude of the centre point (WGS 84). Needs lat.") Double lon,
        @Parameter(description = "Radius around (lat, lon) in metres, up to 500000. Not with bbox.") Double radius,
        @Parameter(description = "Bounding box minLon,minLat,maxLon,maxLat. Not with radius; must not cross "
                + "the antimeridian.") List<Double> bbox,
        @Parameter(description = "Full-text query in web-search syntax: words, \"phrases\", -exclusions, OR.")
        String q,
        @Parameter(description = "Lowest price in NAD") BigDecimal minPrice,
        @Parameter(description = "Highest price in NAD") BigDecimal maxPrice,
        @Parameter(description = "One or more categories (any of them matches)") List<Category> category,
        @Parameter(description = "One or more difficulties (any of them matches)") List<Difficulty> difficulty,
        @Parameter(description = "Shortest duration in minutes") Integer minDuration,
        @Parameter(description = "Longest duration in minutes") Integer maxDuration,
        @Parameter(description = "Lowest average rating, 0 to 5") BigDecimal minRating,
        @Parameter(description = "Has a departure on or after this date (Africa/Windhoek)")
        @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
        @Parameter(description = "Has a departure on or before this date (Africa/Windhoek)")
        @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to,
        @Parameter(description = "That departure has at least this many seats left (default 1 with from/to)")
        Integer minSlots,
        @Parameter(description = "relevance | distance | price_asc | price_desc | rating | newest. Default: "
                + "relevance with q, else distance with a point, else newest.") SearchSort sort,
        @Parameter(description = "Page size, 1 to 100 (default 20)") Integer size,
        @Parameter(description = "nextCursor from the previous page; send the same filters with it") String cursor) {
}
