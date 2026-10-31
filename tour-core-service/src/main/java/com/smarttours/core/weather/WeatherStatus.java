package com.smarttours.core.weather;

import com.fasterxml.jackson.annotation.JsonValue;

import java.util.Locale;

/** What {@code weather} in a response is. Clients branch on this, never on HTTP status: weather is always 200. */
public enum WeatherStatus {
    /** Fresh from the provider or the cache. */
    OK,
    /** The provider is down or its circuit is open: the last value we had, marked {@code stale: true}. */
    STALE,
    /** Nothing to show: the provider failed and nothing was cached. {@code weather} is null. */
    UNAVAILABLE,
    /** Tour detail with include=weather, no date given and the tour has no upcoming departure. */
    NO_DEPARTURE;

    @JsonValue
    public String json() {
        return name().toLowerCase(Locale.ROOT);
    }
}
