package com.smarttours.core.weather;

import com.smarttours.core.availability.DepartureSummaryRepository;
import com.smarttours.core.common.InvalidParameterException;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * One budget for the whole tour-detail weather step, the departure query and the cache read included, not just
 * the provider call. The provider never answers here, so every lookup ends at the budget; what is measured is
 * how long the page waits. Timings are generous (the CI machine may be slow) but tight enough that the old
 * behaviour, budget plus the cache read plus the query, fails them.
 */
class WeatherBudgetTest {

    private static final Duration BUDGET = Duration.ofMillis(400);
    /** Scheduling slack on a busy CI machine; the old behaviour overshoots by 300 ms or more. */
    private static final Duration SLACK = Duration.ofMillis(150);
    private static final ZoneId ZONE = ZoneId.of("Africa/Windhoek");
    private static final Clock CLOCK = Clock.fixed(Instant.parse("2030-11-20T08:00:00Z"), ZoneOffset.UTC);
    private static final LocalDate DEPARTURE = LocalDate.of(2030, 11, 25);

    private final WeatherProvider provider = mock(WeatherProvider.class);
    private final WeatherCache cache = mock(WeatherCache.class);
    private final DepartureSummaryRepository departures = mock(DepartureSummaryRepository.class);
    private final SimpleMeterRegistry meters = new SimpleMeterRegistry();
    private final WeatherProperties properties = new WeatherProperties(16, 10, 7, 5, Duration.ofHours(3),
            Duration.ofDays(2), Duration.ofDays(30), Duration.ofDays(90), 0.1, BUDGET, Duration.ofSeconds(3), ZONE);
    private DepartureWeather departureWeather;

    @BeforeEach
    void setUp() {
        when(provider.forecast(any(), any())).thenReturn(new CompletableFuture<>()); // never answers
        when(departures.nextDepartureDate(anyLong(), any())).thenReturn(Optional.of(DEPARTURE));
        WeatherService weather = new WeatherService(provider, cache, properties, CLOCK, meters);
        departureWeather = new DepartureWeather(weather, departures, properties, meters);
    }

    @AfterEach
    void tearDown() {
        departureWeather.shutdown();
    }

    @Test
    void aSlowCacheReadShortensTheProviderWaitInsteadOfAddingToIt() {
        when(cache.get(anyString())).thenAnswer(invocation -> sleep(Duration.ofMillis(300), null));

        Timed timed = timed(() -> departureWeather.forTour(1, -24.7, 15.3, null));

        assertThat(timed.lookup().status()).isEqualTo(WeatherStatus.UNAVAILABLE);
        assertThat(timed.took()).isLessThan(BUDGET.plus(SLACK)); // was 300 + 400 ms
    }

    @Test
    void aSlowDepartureQueryIsCutOffAtTheBudget() {
        when(departures.nextDepartureDate(anyLong(), any()))
                .thenAnswer(invocation -> sleep(Duration.ofSeconds(2), Optional.of(DEPARTURE)));

        Timed timed = timed(() -> departureWeather.forTour(1, -24.7, 15.3, null));

        assertThat(timed.lookup().status()).isEqualTo(WeatherStatus.UNAVAILABLE);
        assertThat(timed.took()).isLessThan(BUDGET.plus(SLACK)); // was 2 s + 400 ms
        assertThat(meters.counter("weather.stage.over.budget").count()).isEqualTo(1.0);
    }

    @Test
    void anExpiredEntryIsStillServedStaleWithinTheBudget() {
        WeatherReport report = WeatherReport.forecast(GeoCell.of(-24.7, 15.3), new DailyWeather(DEPARTURE,
                "Africa/Windhoek", 31.0, 14.0, 5, 0.0, 18.0, 0, LocalTime.of(5, 40), LocalTime.of(19, 10)),
                CLOCK.instant().minus(Duration.ofDays(1)));
        when(cache.get(anyString())).thenAnswer(invocation -> sleep(Duration.ofMillis(100),
                new WeatherCache.Entry(report, CLOCK.instant().minusSeconds(60))));

        Timed timed = timed(() -> departureWeather.forTour(1, -24.7, 15.3, null));

        assertThat(timed.lookup().status()).isEqualTo(WeatherStatus.STALE);
        assertThat(timed.lookup().weather().stale()).isTrue();
        assertThat(timed.took()).isLessThan(BUDGET.plus(SLACK));
        assertThat(meters.counter("weather.stage.over.budget").count()).isZero();
    }

    @Test
    void aPastDateIsStillTheCallersMistake() {
        assertThatThrownBy(() -> departureWeather.forTour(1, -24.7, 15.3, LocalDate.of(2030, 11, 1)))
                .isInstanceOf(InvalidParameterException.class);
    }

    private record Timed(WeatherLookup lookup, Duration took) {
    }

    private static Timed timed(java.util.function.Supplier<WeatherLookup> step) {
        long start = System.nanoTime();
        WeatherLookup lookup = step.get();
        return new Timed(lookup, Duration.ofNanos(System.nanoTime() - start));
    }

    private static <T> T sleep(Duration duration, T value) throws InterruptedException {
        Thread.sleep(duration);
        return value;
    }
}
