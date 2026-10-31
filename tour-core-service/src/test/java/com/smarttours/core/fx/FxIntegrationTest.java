package com.smarttours.core.fx;

import com.github.tomakehurst.wiremock.client.WireMock;
import com.smarttours.core.integration.ProviderTestSupport;
import com.smarttours.core.support.ProviderStubsIntegrationTest;
import com.smarttours.core.support.TourFixtures;
import io.micrometer.core.instrument.MeterRegistry;
import net.javacrumbs.shedlock.core.DefaultLockingTaskExecutor;
import net.javacrumbs.shedlock.core.LockConfiguration;
import net.javacrumbs.shedlock.core.LockingTaskExecutor;
import net.javacrumbs.shedlock.provider.jdbctemplate.JdbcTemplateLockProvider;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.json.JsonMapper;

import javax.sql.DataSource;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;

import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.okJson;
import static com.github.tomakehurst.wiremock.client.WireMock.serverError;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The rate refresh and display prices end to end: Frankfurter on WireMock (recorded responses), the real
 * fx_rates and shedlock tables on PostGIS, and the real Redis.
 */
class FxIntegrationTest extends ProviderStubsIntegrationTest {

    private static final String RATES = "/v2/rates";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private StringRedisTemplate redis;

    @Autowired
    private ExchangeRateRefresher refresher;

    @Autowired
    private FxRefreshJob job;

    @Autowired
    private ExchangeRates rates;

    @Autowired
    private FxRateRepository repository;

    @Autowired
    private FxProperties properties;

    @Autowired
    private DataSource dataSource;

    @Autowired
    private JsonMapper json;

    @Autowired
    private MeterRegistry meters;

    private LocalDate today;

    @BeforeEach
    void clean() {
        jdbc.sql("TRUNCATE tours, tour_departure_summary, fx_rates, shedlock RESTART IDENTITY CASCADE").update();
        rates.evictLocal();
        today = LocalDate.now(Clock.system(properties.zone()));
    }

    @Test
    void anAcceptedRefreshIsStoredSharedAndUsedForDisplayPrices() throws Exception {
        stubRates("frankfurter/rates-2026-10-31.json");

        assertThat(refresher.refresh()).isEqualTo(ExchangeRateRefresher.Outcome.ACCEPTED);

        assertThat(jdbc.sql("SELECT count(*) FROM fx_rates WHERE source = 'frankfurter:blended'").query(Long.class)
                .single()).isEqualTo(5);
        assertThat(redis.opsForValue().get("fx:latest:NAD")).contains("\"EUR\"");
        long tour = createTour("1850.00");
        mockMvc.perform(get("/api/tours/{id}", tour).param("currency", "EUR"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.price").value(1850.00))
                .andExpect(jsonPath("$.displayPrice.amount").value(99.58))
                .andExpect(jsonPath("$.displayPrice.rate").value(0.053827))
                .andExpect(jsonPath("$.displayPrice.rateAsOf").value(today.toString()))
                .andExpect(jsonPath("$.displayPrice.stale").value(false));
    }

    @Test
    void aRefreshThatFailsTheSanityCheckKeepsTheRatesInUse() throws Exception {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        double rejectedBefore = counter("fx.refresh.rejections", "reason", "peg_violation");

        PROVIDERS.resetAll();
        stubRates("frankfurter/rates-bna-peg-off.json");

        assertThat(refresher.refresh()).isEqualTo(ExchangeRateRefresher.Outcome.REJECTED);
        assertThat(counter("fx.refresh.rejections", "reason", "peg_violation")).isEqualTo(rejectedBefore + 1);
        assertThat(repository.latest("NAD").orElseThrow().quote("ZAR").orElseThrow().rate())
                .isEqualByComparingTo("1.0005");
        mockMvc.perform(get("/api/tours/{id}", createTour("1850.00")).param("currency", "EUR"))
                .andExpect(jsonPath("$.displayPrice.rate").value(0.053827));
    }

    @Test
    void aProviderOutageKeepsTheRatesInUse() {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        PROVIDERS.resetAll();
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(RATES)).willReturn(serverError()));

        assertThat(refresher.refresh()).isEqualTo(ExchangeRateRefresher.Outcome.FAILED);
        assertThat(repository.latest("NAD").orElseThrow().quotes()).hasSize(5);
    }

    /**
     * Two schedulers fire together: this instance's job twice at once, and a second "instance" with its own
     * lock provider on the same table. The provider is slow, so every run overlaps the first. One fetch.
     */
    @Test
    void shedLockLetsOneOfTwoSchedulersRefresh() throws Exception {
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(RATES)).willReturn(okJson(rates("frankfurter/rates-2026-10-31.json"))
                .withFixedDelay(1_500)));
        LockingTaskExecutor otherInstance = new DefaultLockingTaskExecutor(new JdbcTemplateLockProvider(
                JdbcTemplateLockProvider.Configuration.builder().withJdbcTemplate(new JdbcTemplate(dataSource))
                        .usingDbTime().build()));
        CountDownLatch go = new CountDownLatch(1);

        CompletableFuture<?>[] runs = {
                CompletableFuture.runAsync(() -> awaitThen(go, job::scheduledRefresh)),
                CompletableFuture.runAsync(() -> awaitThen(go, job::scheduledRefresh)),
                CompletableFuture.runAsync(() -> awaitThen(go, () -> otherInstance.executeWithLock(
                        (Runnable) refresher::refresh, new LockConfiguration(Instant.now(), FxRefreshJob.LOCK,
                                Duration.ofMinutes(10), Duration.ofMinutes(1)))))
        };
        go.countDown();
        CompletableFuture.allOf(runs).join();

        PROVIDERS.verify(1, getRequestedFor(urlPathEqualTo(RATES)));
        assertThat(jdbc.sql("SELECT count(*) FROM fx_rates").query(Long.class).single()).isEqualTo(5);
        assertThat(jdbc.sql("SELECT count(*) FROM shedlock WHERE name = 'fx-refresh'").query(Long.class).single())
                .isEqualTo(1);
    }

    @Test
    void theStartUpRefreshSkipsWhenRatesWereFetchedToday() {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        jdbc.sql("TRUNCATE shedlock").update();
        PROVIDERS.resetRequests();

        job.refreshIfMissing();

        PROVIDERS.verify(0, getRequestedFor(urlPathEqualTo(RATES)));
    }

    @Test
    void withRedisDownRatesComeFromTheDatabase() {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        LettuceConnectionFactory nowhere = new LettuceConnectionFactory(new RedisStandaloneConfiguration("127.0.0.1", 9));
        nowhere.afterPropertiesSet();
        StringRedisTemplate deadRedis = new StringRedisTemplate(nowhere);
        double errorsBefore = counter("cache.errors", "cache", "fx");

        ExchangeRates onAnotherInstance = new ExchangeRates(repository, deadRedis, json, properties, Clock.systemUTC(), meters);
        Optional<RateSnapshot> snapshot = onAnotherInstance.current();

        assertThat(snapshot).isPresent();
        assertThat(snapshot.get().quote("EUR").orElseThrow().rate()).isEqualByComparingTo("0.053827");
        assertThat(counter("cache.errors", "cache", "fx")).isGreaterThan(errorsBefore);
        nowhere.destroy();
    }

    @Test
    void withRedisEmptiedRatesComeFromTheDatabaseAndAreWrittenBack() {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        redis.delete("fx:latest:NAD");
        rates.evictLocal();

        assertThat(rates.current()).isPresent();
        assertThat(redis.hasKey("fx:latest:NAD")).isTrue();
    }

    /**
     * maxPrice=80 EUR is 1486.25 NAD (rounded up), so only the 1000 NAD tour matches. The cached first page
     * holds NAD only: the same search in NAD is a cache hit, and has no display price.
     */
    @Test
    void searchBoundsInTheDisplayCurrencyAreConvertedAndTheCacheStaysCurrencyFree() throws Exception {
        stubRates("frankfurter/rates-2026-10-31.json");
        refresher.refresh();
        createTour("1000.00");
        createTour("2000.00");

        mockMvc.perform(get("/api/tours/search").param("maxPrice", "80").param("currency", "EUR"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.size").value(1))
                .andExpect(jsonPath("$.items[0].price").value(1000.00))
                .andExpect(jsonPath("$.items[0].displayPrice.amount").value(53.83))
                .andExpect(jsonPath("$.items[0].displayPrice.currency").value("EUR"));

        String cached = redis.opsForValue().get(redis.keys("search::*").iterator().next());
        assertThat(cached).contains("\"price\":1000.00").doesNotContain("displayPrice");

        // Renamed behind the cache's back: the NAD search still sees the old name, so it was a cache hit
        jdbc.sql("UPDATE tours SET name = 'Changed in the database only'").update();
        mockMvc.perform(get("/api/tours/search").param("maxPrice", "1486.25"))
                .andExpect(jsonPath("$.items[0].name").value("Sossusvlei Sunrise Dunes"))
                .andExpect(jsonPath("$.items[0].displayPrice").doesNotExist());
    }

    @Test
    void searchBoundsInACurrencyWithoutUsableRatesAre503() throws Exception {
        mockMvc.perform(get("/api/tours/search").param("maxPrice", "80").param("currency", "USD"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.type").value("https://smarttours.dev/problems/exchange-rates-unavailable"))
                .andExpect(jsonPath("$.currency").value("USD"));
        // Without bounds the search works, in NAD, and says why there is no USD price
        createTour("1000.00");
        mockMvc.perform(get("/api/tours/search").param("currency", "USD"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].displayPrice").doesNotExist())
                .andExpect(jsonPath("$.items[0].displayPriceUnavailable")
                        .value("No exchange rate for USD has been loaded yet"));
    }

    @Test
    void ratesOlderThanTheMaximumAgeAreNotUsed() throws Exception {
        LocalDate old = today.minusDays(properties.maxAgeDays() + 1);
        repository.save(new FetchedRates("NAD", "test", java.util.List.of(
                new FetchedRates.Rate("EUR", new BigDecimal("0.053827"), old))), Instant.now());

        mockMvc.perform(get("/api/tours/{id}", createTour("1850.00")).param("currency", "eur"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayPrice").doesNotExist())
                .andExpect(jsonPath("$.displayPriceUnavailable").value("The newest EUR rate is from " + old + ", "
                        + (properties.maxAgeDays() + 1) + " days old; rates older than 14 days are not used"));
    }

    @Test
    void anUnsupportedCurrencyIs400ListingTheSupportedOnes() throws Exception {
        mockMvc.perform(get("/api/tours/search").param("currency", "JPY"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.supportedCurrencies.length()").value(6));
    }

    private void stubRates(String fixture) {
        PROVIDERS.stubFor(WireMock.get(urlPathEqualTo(RATES)).willReturn(okJson(rates(fixture))));
    }

    /** The recorded response, re-dated to today so staleness doesn't depend on when the tests run. */
    private String rates(String fixture) {
        return ProviderTestSupport.fixture(fixture).replaceAll("\"date\":\"\\d{4}-\\d{2}-\\d{2}\"",
                "\"date\":\"" + today + "\"");
    }

    private long createTour(String price) throws Exception {
        String location = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("1850.00", price)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getHeader(HttpHeaders.LOCATION);
        return Long.parseLong(location.substring(location.lastIndexOf('/') + 1));
    }

    private double counter(String name, String... tags) {
        var counter = meters.find(name).tags(tags).counter();
        if (counter != null) {
            return counter.count();
        }
        var functionCounter = meters.find(name).tags(tags).functionCounter();
        return functionCounter == null ? 0 : functionCounter.count();
    }

    private static void awaitThen(CountDownLatch go, Runnable action) {
        try {
            go.await();
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(ex);
        }
        action.run();
    }
}
