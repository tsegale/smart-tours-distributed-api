package com.smarttours.core.weather;

import java.time.LocalDate;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/**
 * Port: daily weather for a place. Asynchronous, because it is called on the request path and the caller
 * decides how long it can wait; implementations apply their own resilience policy, so a returned future
 * always completes, with a value or a failure, within that policy's bounds.
 */
public interface WeatherProvider {

    /** The forecast for one day within the provider's forecast horizon. */
    CompletableFuture<DailyWeather> forecast(GeoCell cell, LocalDate date);

    /** Observed (reanalysis) weather for each day of a past range, inclusive. */
    CompletableFuture<List<DailyWeather>> history(GeoCell cell, LocalDate from, LocalDate to);
}
