package com.smarttours.core.fx.frankfurter;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.client.ResponseDefinitionBuilder;
import com.github.tomakehurst.wiremock.http.Fault;
import com.smarttours.core.fx.FetchedRates;
import com.smarttours.core.integration.ProviderHttpException;
import com.smarttours.core.integration.ProviderProperties;
import com.smarttours.core.integration.ProviderTestSupport;
import com.smarttours.core.integration.ProviderUnavailableException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Set;
import java.util.concurrent.TimeoutException;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static com.github.tomakehurst.wiremock.stubbing.Scenario.STARTED;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** The Frankfurter adapter against WireMock: the recorded v2 format and each failure mode. */
class FrankfurterExchangeRateProviderTest {

    private static final String RATES = "/v2/rates";
    private static final Set<String> QUOTES = Set.of("ZAR", "USD", "EUR", "GBP", "BWP");

    private static final WireMockServer wireMock = new WireMockServer(options().dynamicPort());

    @BeforeAll
    static void startAndWarmUp() {
        wireMock.start();
        stub(okJson(ProviderTestSupport.fixture("frankfurter/rates-2026-10-31.json")));
        provider(ProviderTestSupport.warmUp(wireMock.baseUrl())).latest("NAD", QUOTES);
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
    void parsesTheRecordedV2RatesAndAsksForTheQuotesInAStableOrder() {
        stub(okJson(ProviderTestSupport.fixture("frankfurter/rates-2026-10-31.json")));

        FetchedRates fetched = provider(ProviderTestSupport.provider(wireMock.baseUrl())).latest("NAD", QUOTES);

        LocalDate day = LocalDate.parse("2026-10-31");
        assertThat(fetched.base()).isEqualTo("NAD");
        assertThat(fetched.source()).isEqualTo("frankfurter:blended");
        assertThat(fetched.rates()).containsExactly(
                new FetchedRates.Rate("BWP", new BigDecimal("0.81569"), day),
                new FetchedRates.Rate("EUR", new BigDecimal("0.053827"), day),
                new FetchedRates.Rate("GBP", new BigDecimal("0.044991"), day),
                new FetchedRates.Rate("USD", new BigDecimal("0.060149"), day),
                new FetchedRates.Rate("ZAR", new BigDecimal("1.0005"), day));
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(RATES))
                .withQueryParam("base", equalTo("NAD"))
                .withQueryParam("quotes", equalTo("BWP,EUR,GBP,USD,ZAR"))
                .withHeader("User-Agent", equalTo(ProviderTestSupport.USER_AGENT)));
    }

    /** The real 422 for an unknown currency: not retried, not held against the provider. */
    @Test
    void anInvalidCurrencyIsAClientErrorAndNotRetried() {
        stub(aResponse().withStatus(422).withHeader("Content-Type", "application/json")
                .withBody(ProviderTestSupport.fixture("frankfurter/error-invalid-currency.json")));
        FrankfurterExchangeRateProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl()));

        assertThatThrownBy(() -> provider.latest("NAD", Set.of("XYZ")))
                .isInstanceOf(ProviderHttpException.class)
                .hasMessage("frankfurter answered 422: {\"status\":422,\"message\":\"invalid currency: XYZ\"}");
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(RATES)));
        assertThat(provider.policy().circuitBreaker().getMetrics().getNumberOfFailedCalls()).isZero();
    }

    @Test
    void malformedJsonIsNotRetried() {
        stub(aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody("[{\"date\":"));

        assertThatThrownBy(() -> provider(ProviderTestSupport.provider(wireMock.baseUrl())).latest("NAD", QUOTES))
                .isInstanceOf(RestClientException.class);
        wireMock.verify(1, getRequestedFor(urlPathEqualTo(RATES)));
    }

    @Test
    void serviceUnavailableIsRetriedUntilItRecovers() {
        wireMock.stubFor(get(urlPathEqualTo(RATES)).inScenario("deploy").whenScenarioStateIs(STARTED)
                .willReturn(aResponse().withStatus(503)).willSetStateTo("up"));
        wireMock.stubFor(get(urlPathEqualTo(RATES)).inScenario("deploy").whenScenarioStateIs("up")
                .willReturn(okJson(ProviderTestSupport.fixture("frankfurter/rates-2026-10-31.json"))));

        assertThat(provider(ProviderTestSupport.provider(wireMock.baseUrl())).latest("NAD", QUOTES).rates()).hasSize(5);
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(RATES)));
    }

    @Test
    void tooManyRequestsWithAnHttpDateRetryAfterIsHonoured() {
        String inTwoSeconds = DateTimeFormatter.RFC_1123_DATE_TIME.format(ZonedDateTime.now(ZoneOffset.UTC).plusSeconds(2));
        wireMock.stubFor(get(urlPathEqualTo(RATES)).inScenario("limited").whenScenarioStateIs(STARTED)
                .willReturn(aResponse().withStatus(429).withHeader("Retry-After", inTwoSeconds)).willSetStateTo("ok"));
        wireMock.stubFor(get(urlPathEqualTo(RATES)).inScenario("limited").whenScenarioStateIs("ok")
                .willReturn(okJson(ProviderTestSupport.fixture("frankfurter/rates-2026-10-31.json"))));

        long started = System.nanoTime();
        provider(ProviderTestSupport.provider(wireMock.baseUrl())).latest("NAD", QUOTES);

        // An HTTP-date has whole seconds, so the wait is somewhere in (1 s, 2 s]
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isGreaterThan(Duration.ofMillis(900));
        wireMock.verify(2, getRequestedFor(urlPathEqualTo(RATES)));
    }

    @Test
    void timeoutsAndResetsAreRetriedThenGivenUp() {
        stub(okJson("[]").withFixedDelay(1_500));
        ProviderProperties tight = ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofMillis(300), 3, 10,
                Duration.ofSeconds(30), 1, 2);
        // The time limit is a checked TimeoutException; the blocking adapter rethrows it unchecked
        assertThatThrownBy(() -> provider(tight).latest("NAD", QUOTES))
                .isInstanceOf(ProviderUnavailableException.class)
                .hasCauseInstanceOf(TimeoutException.class);

        wireMock.resetAll();
        stub(aResponse().withFault(Fault.CONNECTION_RESET_BY_PEER));
        assertThatThrownBy(() -> provider(ProviderTestSupport.provider(wireMock.baseUrl(), Duration.ofSeconds(1), 3, 10,
                Duration.ofSeconds(30), 1, 2)).latest("NAD", QUOTES))
                .isInstanceOf(ResourceAccessException.class);
        wireMock.verify(3, getRequestedFor(urlPathEqualTo(RATES)));
    }

    @Test
    void aDeadProviderOpensTheCircuit() {
        stub(aResponse().withFault(Fault.EMPTY_RESPONSE));
        FrankfurterExchangeRateProvider provider = provider(ProviderTestSupport.provider(wireMock.baseUrl(),
                Duration.ofSeconds(1), 2, 4, Duration.ofMinutes(5), 1, 2));

        assertThatThrownBy(() -> provider.latest("NAD", QUOTES)).isInstanceOf(ResourceAccessException.class);
        assertThatThrownBy(() -> provider.latest("NAD", QUOTES)).isInstanceOf(ResourceAccessException.class);

        assertThat(provider.policy().circuitBreaker().getState()).isEqualTo(CircuitBreaker.State.OPEN);
        assertThatThrownBy(() -> provider.latest("NAD", QUOTES)).isInstanceOf(CallNotPermittedException.class);
        wireMock.verify(4, getRequestedFor(urlPathEqualTo(RATES)));
    }

    private static void stub(ResponseDefinitionBuilder response) {
        wireMock.stubFor(get(urlPathEqualTo(RATES)).willReturn(response));
    }

    private static FrankfurterExchangeRateProvider provider(ProviderProperties settings) {
        var integrations = ProviderTestSupport.integrations(settings, settings, settings);
        return new FrankfurterExchangeRateProvider(ProviderTestSupport.clients(integrations),
                ProviderTestSupport.policies(), integrations);
    }
}
