package com.smarttours.core.weather;

import com.github.tomakehurst.wiremock.client.ResponseDefinitionBuilder;
import com.github.tomakehurst.wiremock.client.WireMock;
import com.github.tomakehurst.wiremock.http.Fault;
import com.smarttours.core.integration.ProviderTestSupport;
import com.smarttours.core.support.ProviderStubsIntegrationTest;
import com.smarttours.core.support.TourFixtures;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.stream.IntStream;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.serverError;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Departure weather end to end: Open-Meteo on WireMock (recorded responses re-dated to the test's dates), the
 * real Redis cache and the real departure read model. The theme: the tour page never fails or slows down
 * because of weather.
 */
class WeatherIntegrationTest extends ProviderStubsIntegrationTest {

    private static final String FORECAST = "/v1/forecast";
    private static final String ARCHIVE = "/v1/archive";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private WeatherCache cache;

    @Autowired
    private WeatherProperties properties;

    private LocalDate today;
    private long tourId;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.sql("TRUNCATE tours, tour_departure_summary RESTART IDENTITY CASCADE").update();
        today = LocalDate.now(Clock.system(properties.zone()));
        tourId = createTour();
    }

    @Test
    void aDateWithinTheHorizonGetsTheForecastWithAttributionAndIsCached() throws Exception {
        LocalDate date = today.plusDays(5);
        stubForecast(okJson(forecastFor(date)));

        for (int i = 0; i < 2; i++) {
            mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", date.toString()))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.tourId").value(tourId))
                    .andExpect(jsonPath("$.weatherStatus").value("ok"))
                    .andExpect(jsonPath("$.weather.type").value("forecast"))
                    .andExpect(jsonPath("$.weather.date").value(date.toString()))
                    .andExpect(jsonPath("$.weather.temperatureMaxC").value(34.4))
                    .andExpect(jsonPath("$.weather.precipitationProbabilityPercent").value(0))
                    .andExpect(jsonPath("$.weather.description").value("Clear sky"))
                    .andExpect(jsonPath("$.weather.timezone").value("Africa/Windhoek"))
                    .andExpect(jsonPath("$.weather.stale").value(false))
                    .andExpect(jsonPath("$.attribution.text").value("Weather data by Open-Meteo.com"))
                    .andExpect(jsonPath("$.attribution.licenceUrl").value("https://creativecommons.org/licenses/by/4.0/"));
        }
        PROVIDERS.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)).withQueryParam("start_date", equalTo(date.toString())));
    }

    @Test
    void theLastDayOfTheHorizonIsStillAForecastAndTheNextIsAClimateNormal() throws Exception {
        LocalDate lastForecastDay = today.plusDays(properties.forecastHorizonDays());
        stubForecast(okJson(forecastFor(lastForecastDay)));
        stubArchive(okJson(ProviderTestSupport.fixture("open-meteo/archive-sossusvlei-2025-12-01.json")));

        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", lastForecastDay.toString()))
                .andExpect(jsonPath("$.weather.type").value("forecast"));
        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", lastForecastDay.plusDays(1).toString()))
                .andExpect(jsonPath("$.weather.type").value("climate_normal"));
    }

    /** Ten years of the recorded archive week (2025-12-01 to 07): the normal is that week's average. */
    @Test
    void aFarDateGetsTheClimateNormalOfTheLastTenYears() throws Exception {
        stubArchive(okJson(ProviderTestSupport.fixture("open-meteo/archive-sossusvlei-2025-12-01.json")));
        LocalDate farAway = today.plusYears(1).withMonth(12).withDayOfMonth(4);

        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", farAway.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.weatherStatus").value("ok"))
                .andExpect(jsonPath("$.weather.type").value("climate_normal"))
                .andExpect(jsonPath("$.weather.years.count").value(10))
                .andExpect(jsonPath("$.weather.years.to").value(today.getYear() - (farAway.withYear(today.getYear())
                        .plusDays(3).isAfter(today.minusDays(properties.archiveDelayDays())) ? 1 : 0)))
                .andExpect(jsonPath("$.weather.temperatureMaxC").value(32.7))   // 229.0 / 7
                .andExpect(jsonPath("$.weather.temperatureMinC").value(16.1))   // 112.8 / 7
                .andExpect(jsonPath("$.weather.precipitationProbabilityPercent").value(0)) // no day reached 1 mm
                .andExpect(jsonPath("$.weather.weatherCode").value(0))
                .andExpect(jsonPath("$.weather.sunrise").value("06:01:00"));
        PROVIDERS.verify(10, getRequestedFor(urlPathEqualTo(ARCHIVE)));
    }

    @Test
    void aClimateNormalIsBuiltFromTheYearsThatAnsweredIfHalfDid() throws Exception {
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(ARCHIVE)).atPriority(5)
                .willReturn(okJson(ProviderTestSupport.fixture("open-meteo/archive-sossusvlei-2025-12-01.json"))));
        // Years ending in 1 or 3 (two of any ten) answer 400: not retried and not held against the provider, so
        // the breaker stays out of it and exactly those two years are missing. (Transport failures in five years
        // would trip the archive breaker mid-normal, as they should, and make the count depend on timing.)
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(ARCHIVE)).atPriority(1)
                .withQueryParam("start_date", WireMock.matching("\\d{3}[13]-.*"))
                .willReturn(aResponse().withStatus(400).withHeader("Content-Type", "application/json")
                        .withBody("{\"error\":true,\"reason\":\"No data is available for this location\"}")));

        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", today.plusDays(200).toString()))
                .andExpect(jsonPath("$.weatherStatus").value("ok"))
                .andExpect(jsonPath("$.weather.years.count").value(8));
    }

    @Test
    void aPastDateIs400() throws Exception {
        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", today.minusDays(1).toString()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.type").value("https://smarttours.dev/problems/invalid-parameter"))
                .andExpect(jsonPath("$.param").value("date"));
        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", "31-12-2026"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(get("/api/tours/{id}/weather", tourId)).andExpect(status().isBadRequest());
        mockMvc.perform(get("/api/tours/{id}/weather", 999_999).param("date", today.toString()))
                .andExpect(status().isNotFound());
        PROVIDERS.verify(0, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void whenTheProviderFailsTheLastValueIsServedMarkedStale() throws Exception {
        LocalDate date = today.plusDays(3);
        WeatherReport earlier = new WeatherReport(WeatherReport.Type.FORECAST, date, -24.7, 15.3, "Africa/Windhoek",
                31.0, 17.0, 10, 0.0, 20.0, 1, "Mainly clear", null, null, null, Instant.now().minus(Duration.ofHours(2)),
                false);
        // Fetched two hours ago, fresh for one: expired, but still in Redis
        cache.put(WeatherService.forecastKey(GeoCell.of(-24.7275, 15.3424), date), earlier,
                Instant.now().minus(Duration.ofHours(2)), Duration.ofHours(1), 0, Duration.ofHours(24));
        stubForecast(serverError());

        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", date.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.weatherStatus").value("stale"))
                .andExpect(jsonPath("$.weather.stale").value(true))
                .andExpect(jsonPath("$.weather.temperatureMaxC").value(31.0));
    }

    @Test
    void whenTheProviderFailsAndNothingIsCachedWeatherIsNullButTheAnswerIs200() throws Exception {
        stubForecast(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER));

        mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", today.plusDays(2).toString()))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("\"weather\":null")))
                .andExpect(jsonPath("$.weatherStatus").value("unavailable"))
                .andExpect(jsonPath("$.attribution.url").value("https://open-meteo.com/"));
    }

    @Test
    void concurrentMissesForOneKeyMakeOneProviderCall() throws Exception {
        LocalDate date = today.plusDays(4);
        stubForecast(okJson(forecastFor(date)).withFixedDelay(500));
        CountDownLatch go = new CountDownLatch(1);

        List<CompletableFuture<Integer>> requests = IntStream.range(0, 12)
                .mapToObj(i -> CompletableFuture.supplyAsync(() -> {
                    try {
                        go.await();
                        return mockMvc.perform(get("/api/tours/{id}/weather", tourId).param("date", date.toString()))
                                .andReturn().getResponse().getStatus();
                    } catch (Exception ex) {
                        throw new IllegalStateException(ex);
                    }
                }))
                .toList();
        go.countDown();

        assertThat(requests.stream().map(CompletableFuture::join).toList()).containsOnly(200);
        PROVIDERS.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    /**
     * The provider takes 2 s. Tour detail waits only its include budget (400 ms), answers without weather,
     * and the fetch it started completes in the background, so the next page view has the forecast.
     */
    @Test
    void aSlowProviderDoesNotSlowDownTheTourPage() throws Exception {
        LocalDate date = today.plusDays(6);
        stubForecast(okJson(forecastFor(date)).withFixedDelay(1_000));
        PROVIDERS.resetRequests();

        long started = System.nanoTime();
        mockMvc.perform(get("/api/tours/{id}", tourId).param("include", "weather").param("date", date.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("Sossusvlei Sunrise Dunes"))
                .andExpect(jsonPath("$.weatherStatus").value("unavailable"));
        Duration took = Duration.ofNanos(System.nanoTime() - started);

        assertThat(took).isLessThan(properties.includeBudget().plusMillis(400));
        await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                mockMvc.perform(get("/api/tours/{id}", tourId).param("include", "weather").param("date", date.toString()))
                        .andExpect(jsonPath("$.weatherStatus").value("ok"))
                        .andExpect(jsonPath("$.weather.temperatureMaxC").value(34.4)));
        PROVIDERS.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void tourDetailUsesTheNextDepartureWithSeatsWhenNoDateIsGiven() throws Exception {
        LocalDate soldOut = today.plusDays(2);
        LocalDate next = today.plusDays(7);
        departure(1, soldOut, 0);
        departure(2, next, 4);
        departure(3, today.plusDays(9), 4);
        stubForecast(okJson(forecastFor(next)));

        mockMvc.perform(get("/api/tours/{id}", tourId).param("include", "weather"))
                .andExpect(jsonPath("$.weatherStatus").value("ok"))
                .andExpect(jsonPath("$.weather.date").value(next.toString()));
        PROVIDERS.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)).withQueryParam("start_date", equalTo(next.toString())));
    }

    @Test
    void tourDetailWithoutAnUpcomingDepartureSaysSo() throws Exception {
        mockMvc.perform(get("/api/tours/{id}", tourId).param("include", "weather"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.weatherStatus").value("no_departure"));
        PROVIDERS.verify(0, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    private void stubForecast(ResponseDefinitionBuilder response) {
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(FORECAST)).willReturn(response));
    }

    private void stubArchive(ResponseDefinitionBuilder response) {
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(ARCHIVE)).willReturn(response));
    }

    /** The recorded forecast (2026-11-05 at Sossusvlei), moved to {@code date}. */
    private static String forecastFor(LocalDate date) {
        return ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json")
                .replace("2026-11-05", date.toString());
    }

    private void departure(long availabilityId, LocalDate day, int slotsLeft) {
        jdbc.sql("""
                        INSERT INTO tour_departure_summary
                            (availability_id, tour_id, starts_at, total_slots, slots_left, source_version)
                        VALUES (:id, :tour, :startsAt, 4, :left, 1)
                        """)
                .param("id", availabilityId)
                .param("tour", tourId)
                .param("startsAt", OffsetDateTime.of(day.atTime(6, 0), properties.zone().getRules()
                        .getOffset(day.atTime(6, 0))))
                .param("left", slotsLeft)
                .update();
    }

    private long createTour() throws Exception {
        String location = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getHeader(HttpHeaders.LOCATION);
        return Long.parseLong(location.substring(location.lastIndexOf('/') + 1));
    }
}
