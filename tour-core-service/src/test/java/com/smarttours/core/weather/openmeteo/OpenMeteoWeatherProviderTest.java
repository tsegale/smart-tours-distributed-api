package com.smarttours.core.weather.openmeteo;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.http.Fault;
import com.smarttours.core.integration.Failures;
import com.smarttours.core.integration.ProviderHttpException;
import com.smarttours.core.integration.ProviderProperties;
import com.smarttours.core.integration.ProviderTestSupport;
import com.smarttours.core.weather.DailyWeather;
import com.smarttours.core.weather.GeoCell;
import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;

import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalTime;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeoutException;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.serverError;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static com.github.tomakehurst.wiremock.stubbing.Scenario.STARTED;
import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

/**
 * The Open-Meteo adapter and its resilience policy against WireMock: the recorded real format, and every
 * failure mode the policy must handle. Forecast is the provider on the request path, so most failure modes
 * are asserted here; the policy code is shared with the other providers.
 */
class OpenMeteoWeatherProviderTest {

    private static final GeoCell SOSSUSVLEI = GeoCell.of(-24.7275, 15.3424);
    private static final LocalDate DAY = LocalDate.parse("2026-11-05");
    private static final String FORECAST = "/v1/forecast";

    private static final WireMockServer wireMock = new WireMockServer(options().dynamicPort());

    /** Loads the HTTP client and JSON classes once, so no test's time limit is spent on class loading. */
    @BeforeAll
    static void startAndWarmUp() {
        wireMock.start();
        stubForecast(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json")));
        provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY).join();
    }

    @AfterAll
    static void stop() {
        wireMock.stop();
    }

    @BeforeEach
    void reset() {
        wireMock.resetAll();
    }

    @Test
    void parsesTheRecordedForecastAndSendsWhatTheApiExpects() {
        stubForecast(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json")));

        DailyWeather day = provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY).join();

        assertThat(day).isEqualTo(new DailyWeather(DAY, "Africa/Windhoek", 34.4, 18.9, 0, 0.0, 35.1, 0,
                LocalTime.parse("06:08"), LocalTime.parse("19:16")));
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(FORECAST))
                .withQueryParam("latitude", equalTo("-24.7"))
                .withQueryParam("longitude", equalTo("15.3"))
                .withQueryParam("timezone", equalTo("auto"))
                .withQueryParam("start_date", equalTo("2026-11-05"))
                .withQueryParam("end_date", equalTo("2026-11-05"))
                .withQueryParam("daily", equalTo(OpenMeteoWeatherProvider.FORECAST_DAILY))
                .withHeader("User-Agent", equalTo(ProviderTestSupport.USER_AGENT)));
    }

    @Test
    void parsesTheRecordedArchiveWeek() {
        wireMock.stubFor(get(urlPathEqualTo("/v1/archive"))
                .willReturn(okJson(ProviderTestSupport.fixture("open-meteo/archive-sossusvlei-2025-12-01.json"))));

        List<DailyWeather> days = provider(ProviderTestSupport.provider(wireMock.baseUrl()))
                .history(SOSSUSVLEI, LocalDate.parse("2025-12-01"), LocalDate.parse("2025-12-07")).join();

        assertThat(days).hasSize(7);
        assertThat(days.getFirst()).isEqualTo(new DailyWeather(LocalDate.parse("2025-12-01"), "Africa/Windhoek",
                28.5, 13.1, null, 0.0, 22.9, 0, LocalTime.parse("06:01"), LocalTime.parse("19:34")));
        assertThat(days.getLast().weatherCode()).isEqualTo(51);
    }

    @Test
    void malformedJsonFailsAtOnceAndCountsAgainstTheProvider() {
        stubForecast(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("{\"daily\": [tru"));
        OpenMeteoWeatherProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl()));

        Throwable failure = failureOf(provider.forecast(SOSSUSVLEI, DAY));

        assertThat(failure).isInstanceOf(RestClientException.class);
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)));
        assertThat(provider.forecastPolicy().circuitBreaker().getMetrics().getNumberOfFailedCalls()).isEqualTo(1);
    }

    @Test
    void anOkResponseWithoutTheDayIsAProviderFailure() {
        stubForecast(okJson("{\"latitude\":-24.7,\"longitude\":15.3,\"timezone\":\"Africa/Windhoek\"}"));
        OpenMeteoWeatherProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl()));

        assertThat(failureOf(provider.forecast(SOSSUSVLEI, DAY)))
                .isInstanceOf(OpenMeteoWeatherProvider.MalformedWeatherResponseException.class);
        assertThat(provider.forecastPolicy().circuitBreaker().getMetrics().getNumberOfFailedCalls()).isEqualTo(1);
    }

    @Test
    void aSlowProviderTimesOutPerAttemptAndIsRetried() {
        stubForecast(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json"))
                .withFixedDelay(2_000));
        OpenMeteoWeatherProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl(),
                Duration.ofMillis(300), 2, 10, Duration.ofSeconds(30), 4, 8));

        long started = System.nanoTime();
        Throwable failure = failureOf(provider.forecast(SOSSUSVLEI, DAY));
        Duration took = Duration.ofNanos(System.nanoTime() - started);

        // Two attempts of at most 300 ms plus a short backoff, nowhere near the provider's 2 s
        assertThat(failure).isInstanceOfAny(TimeoutException.class, ResourceAccessException.class);
        assertThat(took).isLessThan(Duration.ofMillis(1_500));
        await().atMost(Duration.ofSeconds(3)).untilAsserted(() ->
                wireMock.verify(2, getRequestedFor(urlPathEqualTo(FORECAST))));
    }

    @Test
    void aServerErrorIsRetriedAndTheSecondAttemptWins() {
        wireMock.stubFor(get(urlPathEqualTo(FORECAST)).inScenario("flaky").whenScenarioStateIs(STARTED)
                .willReturn(serverError().withBody("upstream busy")).willSetStateTo("recovered"));
        wireMock.stubFor(get(urlPathEqualTo(FORECAST)).inScenario("flaky").whenScenarioStateIs("recovered")
                .willReturn(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json"))));

        DailyWeather day = provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY).join();

        assertThat(day.temperatureMaxC()).isEqualTo(34.4);
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void tooManyRequestsWaitsForRetryAfterThenRetries() {
        wireMock.stubFor(get(urlPathEqualTo(FORECAST)).inScenario("limited").whenScenarioStateIs(STARTED)
                .willReturn(aResponse().withStatus(429).withHeader("Retry-After", "1")).willSetStateTo("allowed"));
        wireMock.stubFor(get(urlPathEqualTo(FORECAST)).inScenario("limited").whenScenarioStateIs("allowed")
                .willReturn(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json"))));

        long started = System.nanoTime();
        provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY).join();

        // The provider's 1 s, not our 20 ms backoff
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isGreaterThanOrEqualTo(Duration.ofMillis(1_000));
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void aRetryAfterLongerThanWeWillWaitIsNotRetried() {
        stubForecast(aResponse().withStatus(429).withHeader("Retry-After", "120"));

        Throwable failure = failureOf(provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY));

        assertThat(failure).isInstanceOf(ProviderHttpException.class);
        assertThat(((ProviderHttpException) failure).getRetryAfter()).isEqualTo(Duration.ofSeconds(120));
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void aConnectionResetIsRetried() {
        stubForecast(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER));

        Throwable failure = failureOf(provider(ProviderTestSupport.provider(wireMock.baseUrl())).forecast(SOSSUSVLEI, DAY));

        assertThat(failure).isInstanceOf(ResourceAccessException.class);
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    /** The real 400 for a date beyond the horizon: our mistake, so neither retried nor held against the provider. */
    @Test
    void aClientErrorIsNotRetriedAndDoesNotOpenTheCircuit() {
        stubForecast(aResponse().withStatus(400).withHeader("Content-Type", "application/json")
                .withBody(ProviderTestSupport.fixture("open-meteo/forecast-out-of-range.json")));
        OpenMeteoWeatherProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl()));

        Throwable failure = failureOf(provider.forecast(SOSSUSVLEI, DAY));

        assertThat(failure).isInstanceOf(ProviderHttpException.class)
                .hasMessageContaining("out of allowed range from 2026-07-30 to 2026-11-15");
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(FORECAST)));
        assertThat(provider.forecastPolicy().circuitBreaker().getMetrics().getNumberOfFailedCalls()).isZero();
    }

    @Test
    void theCircuitOpensAfterRepeatedFailuresAndThenStopsCallingTheProvider() {
        stubForecast(serverError());
        ProviderProperties settings = ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofMillis(300), 2, 4,
                Duration.ofSeconds(30), 4, 8);
        OpenMeteoWeatherProvider provider = provider(settings);

        failureOf(provider.forecast(SOSSUSVLEI, DAY)); // 2 attempts
        failureOf(provider.forecast(SOSSUSVLEI, DAY)); // 2 more: 4 of 4 failed

        assertThat(provider.forecastPolicy().circuitBreaker().getState()).isEqualTo(CircuitBreaker.State.OPEN);
        long started = System.nanoTime();
        assertThat(failureOf(provider.forecast(SOSSUSVLEI, DAY))).isInstanceOf(CallNotPermittedException.class);
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofMillis(100));
        wireMock.verify(4, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    @Test
    void anOpenCircuitLetsATrialCallThroughAndClosesWhenItSucceeds() {
        stubForecast(serverError());
        ProviderProperties settings = ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofMillis(300), 2, 4,
                Duration.ofMillis(300), 4, 8);
        OpenMeteoWeatherProvider provider = provider(settings);
        CircuitBreaker breaker = provider.forecastPolicy().circuitBreaker();
        failureOf(provider.forecast(SOSSUSVLEI, DAY));
        failureOf(provider.forecast(SOSSUSVLEI, DAY));
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);

        wireMock.resetAll();
        stubForecast(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json")));
        await().atMost(Duration.ofSeconds(2)).until(() -> breaker.getState() == CircuitBreaker.State.HALF_OPEN);

        assertThat(provider.forecast(SOSSUSVLEI, DAY).join().temperatureMaxC()).isEqualTo(34.4);
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
    }

    @Test
    void aTrialCallThatFailsOpensTheCircuitAgain() {
        stubForecast(serverError());
        ProviderProperties settings = ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofMillis(300), 1, 4,
                Duration.ofMillis(300), 4, 8);
        OpenMeteoWeatherProvider provider = provider(settings);
        CircuitBreaker breaker = provider.forecastPolicy().circuitBreaker();
        for (int i = 0; i < 4; i++) {
            failureOf(provider.forecast(SOSSUSVLEI, DAY));
        }
        await().atMost(Duration.ofSeconds(2)).until(() -> breaker.getState() == CircuitBreaker.State.HALF_OPEN);

        failureOf(provider.forecast(SOSSUSVLEI, DAY));

        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);
    }

    @Test
    void aFullBulkheadRejectsAtOnceWithoutBlamingTheProvider() {
        stubForecast(okJson(ProviderTestSupport.fixture("open-meteo/forecast-sossusvlei-2026-11-05.json"))
                .withFixedDelay(250));
        ProviderProperties settings = ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofMillis(1_000), 1, 10,
                Duration.ofSeconds(30), 1, 1);
        OpenMeteoWeatherProvider provider = provider(settings);

        CompletableFuture<DailyWeather> running = provider.forecast(SOSSUSVLEI, DAY);
        CompletableFuture<DailyWeather> queued = provider.forecast(SOSSUSVLEI, DAY);
        Throwable rejected = failureOf(provider.forecast(SOSSUSVLEI, DAY));

        assertThat(rejected).isInstanceOf(BulkheadFullException.class);
        assertThat(running.join().temperatureMaxC()).isEqualTo(34.4);
        assertThat(queued.join().temperatureMaxC()).isEqualTo(34.4);
        assertThat(provider.forecastPolicy().circuitBreaker().getMetrics().getNumberOfFailedCalls()).isZero();
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(FORECAST)));
    }

    private static void stubForecast(com.github.tomakehurst.wiremock.client.ResponseDefinitionBuilder response) {
        wireMock.stubFor(get(urlPathEqualTo(FORECAST)).willReturn(response));
    }

    private static OpenMeteoWeatherProvider provider(ProviderProperties settings) {
        var integrations = ProviderTestSupport.integrations(settings, settings, settings);
        return new OpenMeteoWeatherProvider(ProviderTestSupport.clients(integrations), ProviderTestSupport.policies(),
                integrations);
    }

    private static Throwable failureOf(CompletableFuture<?> future) {
        try {
            future.join();
        } catch (RuntimeException failure) {
            return Failures.unwrap(failure);
        }
        throw new AssertionError("expected the call to fail");
    }
}
