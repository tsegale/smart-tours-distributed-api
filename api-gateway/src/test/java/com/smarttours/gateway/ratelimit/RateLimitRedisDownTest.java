package com.smarttours.gateway.ratelimit;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Redis is stopped mid-test. Search must fail open (served, no X-RateLimit-* headers), booking creation
 * must fail closed (503 Problem Details with Retry-After) and never reach booking-service.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "smarttours.security.jwt-secret=" + RateLimitTestSupport.SECRET)
@Testcontainers(disabledWithoutDocker = true)
class RateLimitRedisDownTest {

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
        REDIS.stop();
    }

    @Value("${local.server.port}")
    private int port;

    @Test
    void searchFailsOpen_bookingCreationFailsClosed() {
        WebTestClient client = WebTestClient.bindToServer().baseUrl("http://localhost:" + port).build();
        String token = RateLimitTestSupport.bearer("dave");

        // With Redis up, both are limited normally.
        client.get().uri("/api/tours/search?q=dunes").header(HttpHeaders.AUTHORIZATION, token).exchange()
                .expectStatus().isOk()
                .expectHeader().exists("X-RateLimit-Remaining");

        REDIS.stop();

        client.get().uri("/api/tours/search?q=dunes").header(HttpHeaders.AUTHORIZATION, token).exchange()
                .expectStatus().isOk()
                .expectHeader().doesNotExist("X-RateLimit-Remaining");
        client.get().uri("/api/tours/1").exchange().expectStatus().isOk();

        String body = client.post().uri("/api/bookings").header(HttpHeaders.AUTHORIZATION, token)
                .contentType(MediaType.APPLICATION_JSON).bodyValue("{\"availability_id\":1,\"slots\":1}")
                .exchange()
                .expectStatus().isEqualTo(503)
                .expectHeader().contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .expectHeader().valueEquals(HttpHeaders.RETRY_AFTER, "1")
                .expectBody(String.class).returnResult().getResponseBody();
        assertThat(body).contains("\"type\":\"https://smarttours.dev/problems/rate-limiter-unavailable\"");

        // Stripe webhooks are not limited at all, so a Redis outage can't hold up payments.
        client.post().uri("/webhooks/stripe").bodyValue("{}").exchange().expectStatus().isOk();
    }
}
