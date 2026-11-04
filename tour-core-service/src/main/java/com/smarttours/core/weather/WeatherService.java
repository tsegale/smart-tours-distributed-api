package com.smarttours.core.weather;

import com.smarttours.core.common.InvalidParameterException;
import com.smarttours.core.integration.Failures;
import com.smarttours.core.integration.SingleFlight;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Weather for a departure date at a tour's location. The tour page must never fail or slow down because of
 * weather, so a lookup never throws (except for a past date, the caller's mistake) and never waits past the
 * caller's deadline, which covers the whole lookup, the cache read included:
 * <ol>
 *   <li>a fresh cache entry is returned at once;</li>
 *   <li>otherwise one fetch per key and instance is started (concurrent misses share it), and awaited for
 *       what is left until the deadline. A fetch that outlives it carries on in the background and fills the
 *       cache;</li>
 *   <li>if it fails or isn't done in time, the expired entry is served marked stale, if there is one;</li>
 *   <li>else {@code weather: null, weatherStatus: unavailable}.</li>
 * </ol>
 * Dates within the forecast horizon get the forecast; later dates the climate normal; past dates 400.
 *
 * <p>Metric: {@code weather_lookups_total{type, outcome=cached|fetched|stale|unavailable}}.
 */
@Service
public class WeatherService {

    private static final Logger log = LoggerFactory.getLogger(WeatherService.class);
    private static final DateTimeFormatter MONTH_DAY = DateTimeFormatter.ofPattern("MM-dd");

    private final WeatherProvider provider;
    private final WeatherCache cache;
    private final WeatherProperties properties;
    private final Clock clock;
    private final MeterRegistry meters;
    private final SingleFlight<WeatherReport> flights = new SingleFlight<>();

    public WeatherService(WeatherProvider provider, WeatherCache cache, WeatherProperties properties, Clock clock,
                          MeterRegistry meters) {
        this.provider = provider;
        this.cache = cache;
        this.properties = properties;
        this.clock = clock;
        this.meters = meters;
    }

    public WeatherLookup lookup(double latitude, double longitude, LocalDate date, Duration budget) {
        return lookup(latitude, longitude, date, Deadline.after(budget));
    }

    public WeatherLookup lookup(double latitude, double longitude, LocalDate date, Deadline deadline) {
        LocalDate today = today();
        if (date.isBefore(today)) {
            throw new InvalidParameterException("date", "date " + date + " is in the past (today is " + today
                    + " in " + properties.zone() + ")");
        }
        GeoCell cell = GeoCell.of(latitude, longitude);
        boolean forecast = !date.isAfter(today.plusDays(properties.forecastHorizonDays()));
        String type = forecast ? "forecast" : "climate_normal";
        List<ClimateNormal.Window> windows = forecast ? List.of() : ClimateNormal.windows(date, today,
                properties.climateYears(), properties.climateWindowDays(), properties.archiveDelayDays());
        String key = forecast ? forecastKey(cell, date) : climateKey(cell, date, windows);

        WeatherCache.Entry cached = cache.get(key);
        if (cached != null && cached.isFresh(clock.instant())) {
            count(type, "cached");
            return WeatherLookup.ok(cached.report());
        }

        CompletableFuture<WeatherReport> fetch = flights.run(key, () -> forecast
                ? fetchForecast(key, cell, date)
                : fetchClimateNormal(key, cell, date, windows));
        // A copy, so the time-out only ends this caller's wait, not the shared fetch. The cache read above has
        // already spent part of the budget: only the rest is waited for.
        Duration wait = deadline.remaining();
        return fetch.copy()
                .orTimeout(wait.toNanos(), TimeUnit.NANOSECONDS)
                .handle((report, failure) -> {
                    if (failure == null) {
                        count(type, "fetched");
                        return WeatherLookup.ok(report);
                    }
                    log.debug("Weather for {} unavailable within {}: {}", key, wait, Failures.unwrap(failure).toString());
                    if (cached != null) {
                        count(type, "stale");
                        return WeatherLookup.stale(cached.report());
                    }
                    count(type, "unavailable");
                    return WeatherLookup.unavailable();
                })
                .join();
    }

    private CompletableFuture<WeatherReport> fetchForecast(String key, GeoCell cell, LocalDate date) {
        return provider.forecast(cell, date).thenApply(day -> {
            WeatherReport report = WeatherReport.forecast(cell, day, clock.instant());
            cache.put(key, report, clock.instant(), properties.forecastTtl(), properties.ttlJitter(),
                    properties.forecastRetention());
            return report;
        });
    }

    /**
     * One archive call per year (a week each: the lightest calls Open-Meteo counts), in parallel. A normal is
     * built from the years that answered, if at least half did; the response says which years those were.
     */
    private CompletableFuture<WeatherReport> fetchClimateNormal(String key, GeoCell cell, LocalDate date,
                                                                List<ClimateNormal.Window> windows) {
        List<CompletableFuture<List<DailyWeather>>> years = windows.stream()
                .map(window -> provider.history(cell, window.from(), window.to())
                        .exceptionally(failure -> {
                            log.warn("Weather history for {} in {} failed, averaging without it: {}", cell.key(),
                                    window.year(), Failures.unwrap(failure).toString());
                            return null;
                        }))
                .toList();
        return CompletableFuture.allOf(years.toArray(CompletableFuture[]::new)).thenApply(done -> {
            List<ClimateNormal.Window> sampled = new ArrayList<>();
            List<DailyWeather> days = new ArrayList<>();
            for (int i = 0; i < windows.size(); i++) {
                List<DailyWeather> year = years.get(i).join();
                if (year != null && !year.isEmpty()) {
                    sampled.add(windows.get(i));
                    days.addAll(year);
                }
            }
            int needed = (windows.size() + 1) / 2;
            if (sampled.size() < needed) {
                throw new IllegalStateException("only " + sampled.size() + " of " + windows.size()
                        + " years of history loaded, need " + needed);
            }
            WeatherReport report = ClimateNormal.of(cell, date, sampled, days, clock.instant());
            cache.put(key, report, clock.instant(), properties.climateTtl(), properties.ttlJitter(),
                    properties.climateRetention());
            return report;
        });
    }

    static String forecastKey(GeoCell cell, LocalDate date) {
        return "weather:forecast:" + cell.key() + ":" + date;
    }

    static String climateKey(GeoCell cell, LocalDate date, List<ClimateNormal.Window> windows) {
        int from = windows.stream().mapToInt(ClimateNormal.Window::year).min().orElse(0);
        int to = windows.stream().mapToInt(ClimateNormal.Window::year).max().orElse(0);
        return "weather:climate:" + cell.key() + ":" + date.format(MONTH_DAY) + ":" + from + "-" + to;
    }

    private LocalDate today() {
        return LocalDate.now(clock.withZone(properties.zone()));
    }

    private void count(String type, String outcome) {
        Counter.builder("weather.lookups")
                .description("Weather lookups by type and how they were answered")
                .tag("type", type)
                .tag("outcome", outcome)
                .register(meters)
                .increment();
    }
}
