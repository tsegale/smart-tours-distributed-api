package com.smarttours.core.search;

import java.util.Arrays;
import java.util.Locale;
import java.util.stream.Collectors;

/**
 * Search orders. Each one is total: ties on the sort key are broken by tour id, in the same
 * direction, so keyset pagination never skips or repeats a row.
 */
public enum SearchSort {
    /** Full-text rank, best first. Needs {@code q}. */
    RELEVANCE(true),
    /** Distance from (lat, lon), nearest first. Needs a point. */
    DISTANCE(false),
    PRICE_ASC(false),
    PRICE_DESC(true),
    /** Average rating, best first. Unrated tours (0.00) come last. */
    RATING(true),
    NEWEST(true);

    private final boolean descending;

    SearchSort(boolean descending) {
        this.descending = descending;
    }

    public boolean descending() {
        return descending;
    }

    /** The query-string form: {@code price_asc}. */
    public String param() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static SearchSort fromParam(String value) {
        for (SearchSort sort : values()) {
            if (sort.param().equalsIgnoreCase(value.trim())) {
                return sort;
            }
        }
        throw new IllegalArgumentException("sort must be one of " + Arrays.stream(values())
                .map(SearchSort::param).collect(Collectors.joining(", ")));
    }
}
