package com.smarttours.gateway.ratelimit;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.EntityExchangeResult;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The real gateway on a random port, real Redis (Testcontainers), stub upstreams. Budgets are tiny, and
 * Redis is flushed before each test, so buckets never carry over between tests.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=" + RateLimitTestSupport.SECRET,
        "RATE_LIMIT_SEARCH_RPS=1", "RATE_LIMIT_SEARCH_BURST=6",
        "RATE_LIMIT_USER_RPS=1", "RATE_LIMIT_USER_BURST=4",
        "RATE_LIMIT_ANONYMOUS_RPS=1", "RATE_LIMIT_ANONYMOUS_BURST=3",
        "RATE_LIMIT_BOOKING_RPS=1", "RATE_LIMIT_BOOKING_BURST=2",
        // The test client connects from 127.0.0.1: trust it as the load balancer for the XFF tests.
        "RATE_LIMIT_TRUSTED_PROXIES=127.0.0.1/32"
})
@Testcontainers(disabledWithoutDocker = true)
class RateLimitIntegrationTest {

    static final GenericContainer<?> REDIS =
            new GenericContainer<>(DockerImageName.parse("redis:7.4-alpine")).withExposedPorts(6379);
    static final HttpServer UPSTREAM = RateLimitTestSupport.okUpstream();

    static {
        REDIS.start();
    }

    @DynamicPropertySource
    static void wiring(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
        String upstream = "http://localhost:" + UPSTREAM.getAddress().getPort();
        registry.add("TOUR_SERVICE_URL", () -> upstream);
        registry.add("BOOKING_SERVICE_URL", () -> upstream);
        registry.add("AI_SERVICE_URL", () -> upstream);
    }

    @AfterAll
    static void stop() {
        UPSTREAM.stop(0);
    }

    @Value("${local.server.port}")
    private int port;

    private WebTestClient client;
    private String clientIp;

    @BeforeEach
    void setUp() throws Exception {
        client = WebTestClient.bindToServer().baseUrl("http://localhost:" + port).build();
        // A distinct "client" per test, via a load balancer the gateway trusts.
        clientIp = "203.0.113." + (1 + (int) (Math.random() * 250));
        // Warm the route under a throwaway key first: a cold first request can take seconds, long enough
        // for the bucket under test to refill a token mid-burst.
        client.get().uri("/api/tours/search?q=warmup").header("X-Forwarded-For", "198.51.100.250").exchange();
        REDIS.execInContainer("redis-cli", "FLUSHALL");
    }

    @Test
    void anonymousSearch_isLimitedPerIp_with429ProblemDetails() {
        assertLimitedAfterBurst(statuses(8, () -> client.get().uri("/api/tours/search?q=dunes")
                .header("X-Forwarded-For", clientIp)), 3);

        EntityExchangeResult<String> refused = client.get().uri("/api/tours/search?q=dunes")
                .header("X-Forwarded-For", clientIp)
                .exchange()
                .expectStatus().isEqualTo(429)
                .expectHeader().contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .expectHeader().valueEquals(HttpHeaders.RETRY_AFTER, "1")
                .expectHeader().valueEquals("X-RateLimit-Remaining", "0")
                .expectHeader().valueEquals("X-RateLimit-Burst-Capacity", "3")
                .expectHeader().valueEquals("X-RateLimit-Replenish-Rate", "1")
                .expectBody(String.class).returnResult();
        assertThat(refused.getResponseBody())
                .contains("\"type\":\"https://smarttours.dev/problems/rate-limited\"", "\"status\":429",
                        "\"instance\":\"/api/tours/search\"");
    }

    @Test
    void allowedResponses_carryTheRemainingBudget() {
        client.get().uri("/api/tours/1").header("X-Forwarded-For", clientIp).exchange()
                .expectStatus().isOk()
                .expectHeader().valueEquals("X-RateLimit-Remaining", "2");
    }

    @Test
    void signedInSearch_hasItsOwnLargerBudget_perUser() {
        String alice = RateLimitTestSupport.bearer("alice-" + UUID.randomUUID());
        String bob = RateLimitTestSupport.bearer("bob-" + UUID.randomUUID());

        assertLimitedAfterBurst(statuses(10, () -> client.get().uri("/api/tours/search?q=dunes")
                .header(HttpHeaders.AUTHORIZATION, alice)), 6);
        // Same address, different user: a separate bucket.
        client.get().uri("/api/tours/search?q=dunes").header(HttpHeaders.AUTHORIZATION, bob).exchange()
                .expectStatus().isOk();
    }

    @Test
    void forgedForwardedFor_fromAnUntrustedPeer_doesNotBuyFreshBudget() {
        // The trusted proxy appends the real client; anything left of it was written by the client.
        assertLimitedAfterBurst(statuses(8, () -> client.get().uri("/api/tours")
                .header("X-Forwarded-For", UUID.randomUUID().toString().substring(0, 8) + ".example, " + clientIp)),
                3);
    }

    @Test
    void bookingCreation_isStrict() {
        String carol = RateLimitTestSupport.bearer("carol-" + UUID.randomUUID());

        assertLimitedAfterBurst(statuses(6, () -> client.post().uri("/api/bookings")
                .header(HttpHeaders.AUTHORIZATION, carol)
                .contentType(MediaType.APPLICATION_JSON).bodyValue("{\"availability_id\":1,\"slots\":1}")), 2);
        // Reading bookings is on the general user budget, not the creation one.
        client.get().uri("/api/bookings/me").header(HttpHeaders.AUTHORIZATION, carol).exchange()
                .expectStatus().isOk();
    }

    @Test
    void stripeWebhooks_areNeverLimited() {
        assertThat(statuses(20, () -> client.post().uri("/webhooks/stripe")
                .header("X-Forwarded-For", clientIp).bodyValue("{}")))
                .containsOnly(200);
    }

    /**
     * The whole burst is served, then requests are refused. The bucket refills at 1 token/s while the test
     * runs, so one later request may be served again; at least half of the excess must still be refused.
     */
    private static void assertLimitedAfterBurst(List<Integer> statuses, int burst) {
        assertThat(statuses.subList(0, burst)).as("the burst is served").containsOnly(200);
        assertThat(statuses.subList(burst, statuses.size())).isSubsetOf(200, 429);
        long refused = statuses.stream().filter(status -> status == 429).count();
        assertThat(refused).isGreaterThanOrEqualTo((statuses.size() - burst + 1) / 2);
    }

    private static List<Integer> statuses(int count, Supplier<WebTestClient.RequestHeadersSpec<?>> request) {
        List<Integer> statuses = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            statuses.add(request.get().exchange().returnResult(String.class).getStatus().value());
        }
        return statuses;
    }
}
