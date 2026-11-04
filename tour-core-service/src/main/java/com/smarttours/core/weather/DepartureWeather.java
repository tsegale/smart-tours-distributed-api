package com.smarttours.core.weather;

import com.smarttours.core.availability.DepartureSummaryRepository;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDate;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Weather embedded in tour detail ({@code include=weather}): for the date asked for, else the tour's next
 * departure with seats left. The whole step, finding the departure, reading the cache and waiting for the
 * provider, gets one budget ({@code smarttours.weather.include-budget}), so the page stays fast:
 * <ul>
 *   <li>the parts share one {@link Deadline}: the provider is waited for only as long as the database query and
 *       the cache read left over, and an expired cache entry can still be served stale before it;</li>
 *   <li>the step runs on a virtual thread and the page stops waiting at the budget even if one synchronous call
 *       (a slow query, a Redis read up to its own time-out) overruns it. That thread finishes on its own, bounded
 *       by those time-outs, and the page answers {@code weatherStatus: unavailable}.</li>
 * </ul>
 * The deadline is set {@link #FALLBACK_MARGIN} before the budget, so a lookup that times out inside still has
 * time to hand back its stale entry before the outer cap would discard it.
 *
 * <p>Metric: {@code weather.stage.over.budget} counts the caps (each one is a synchronous call that overran).
 */
@Service
public class DepartureWeather {

    static final Duration FALLBACK_MARGIN = Duration.ofMillis(25);

    private static final Logger log = LoggerFactory.getLogger(DepartureWeather.class);

    private final WeatherService weather;
    private final DepartureSummaryRepository departures;
    private final WeatherProperties properties;
    private final Counter overBudget;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    public DepartureWeather(WeatherService weather, DepartureSummaryRepository departures, WeatherProperties properties,
                            MeterRegistry meters) {
        this.weather = weather;
        this.departures = departures;
        this.properties = properties;
        this.overBudget = Counter.builder("weather.stage.over.budget")
                .description("Tour-detail weather steps cut off at their budget by a slow synchronous call")
                .register(meters);
    }

    public WeatherLookup forTour(long tourId, double latitude, double longitude, LocalDate requestedDate) {
        Duration budget = properties.includeBudget();
        Deadline deadline = Deadline.after(budget).minus(budget.compareTo(FALLBACK_MARGIN.multipliedBy(4)) > 0
                ? FALLBACK_MARGIN : Duration.ZERO);
        CompletableFuture<WeatherLookup> step = CompletableFuture.supplyAsync(
                () -> lookup(tourId, latitude, longitude, requestedDate, deadline), executor);
        try {
            return step.orTimeout(budget.toNanos(), TimeUnit.NANOSECONDS).join();
        } catch (CompletionException ex) {
            if (ex.getCause() instanceof TimeoutException) {
                overBudget.increment();
                log.warn("Weather for tour {} cut off at its {} budget", tourId, budget);
                return WeatherLookup.unavailable();
            }
            // A past date (the caller's mistake) or a bug: the same exception the caller would get inline.
            throw ex.getCause() instanceof RuntimeException runtime ? runtime : ex;
        }
    }

    private WeatherLookup lookup(long tourId, double latitude, double longitude, LocalDate requestedDate,
                                 Deadline deadline) {
        Optional<LocalDate> date = requestedDate != null
                ? Optional.of(requestedDate)
                : departures.nextDepartureDate(tourId, properties.zone());
        return date.map(day -> weather.lookup(latitude, longitude, day, deadline))
                .orElseGet(WeatherLookup::noDeparture);
    }

    @PreDestroy
    void shutdown() {
        executor.shutdownNow();
    }
}
