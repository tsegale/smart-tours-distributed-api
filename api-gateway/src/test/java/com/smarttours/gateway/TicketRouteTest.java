package com.smarttours.gateway;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.util.Base64;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The ticket and check-in routes end to end, through the real gateway to a stub booking-service: the presigned
 * redirect reaches the client untouched (the gateway must not follow it), and identity, email included, is
 * forwarded. Redis is not running, so these routes' rate limiters fail open, as configured.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=" + TicketRouteTest.SECRET,
        "smarttours.security.dev-tokens.enabled=true",
        "TOUR_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1",
        "spring.data.redis.port=1"
})
class TicketRouteTest {

    static final String SECRET = "qS0t8yQ0bX9mJ3vV6cP2nL5kR8wE1zT4uY7iO0pA3sD6fG9hJ2kL5zX8cV1bN4mQ";
    private static final String PRESIGNED = "http://localhost:9000/tickets/tickets/7/ST-7K3Q9P2M.pdf?X-Amz-Signature=abc";
    private static final AtomicReference<Map<String, List<String>>> RECEIVED_HEADERS = new AtomicReference<>();
    private static final HttpServer BOOKING_SERVICE = startStub();

    @Value("${local.server.port}")
    private int port;

    private WebTestClient client;

    @DynamicPropertySource
    static void bookingService(DynamicPropertyRegistry registry) {
        registry.add("BOOKING_SERVICE_URL", () -> "http://localhost:" + BOOKING_SERVICE.getAddress().getPort());
    }

    @BeforeEach
    void setUp() {
        client = WebTestClient.bindToServer().baseUrl("http://localhost:" + port).build();
        RECEIVED_HEADERS.set(null);
    }

    @AfterAll
    static void stopStub() {
        BOOKING_SERVICE.stop(0);
    }

    @Test
    void theTicketRedirectIsPassedThroughWithIdentityAndEmail() {
        client.get().uri("/api/bookings/7/ticket")
                .header("Authorization", "Bearer " + sign("alice", "CUSTOMER", "alice@example.com"))
                .header("X-User-Email", "victim@example.com")
                .exchange()
                .expectStatus().isFound()
                .expectHeader().valueEquals("Location", PRESIGNED);

        Map<String, List<String>> headers = RECEIVED_HEADERS.get();
        assertThat(headers.get("X-user-id")).containsExactly("alice");
        assertThat(headers.get("X-user-email")).containsExactly("alice@example.com");
    }

    @Test
    void checkInReachesBookingServiceForAnOperator() {
        client.post().uri("/api/check-ins")
                .header("Authorization", "Bearer " + sign("op-1", "OPERATOR", null))
                .bodyValue("{\"token\": \"eyJ\"}")
                .exchange()
                .expectStatus().isOk();

        assertThat(RECEIVED_HEADERS.get().get("X-user-roles")).containsExactly("OPERATOR");
        assertThat(RECEIVED_HEADERS.get()).doesNotContainKey("X-user-email");
    }

    @Test
    void anOversizedCheckInIsRefusedAtTheEdge() {
        byte[] huge = new byte[16 * 1024];
        client.post().uri("/api/check-ins")
                .header("Authorization", "Bearer " + sign("op-1", "OPERATOR", null))
                .header("Content-Length", String.valueOf(huge.length))
                .bodyValue(huge)
                .exchange()
                .expectStatus().isEqualTo(413);

        assertThat(RECEIVED_HEADERS.get()).isNull();
    }

    private static HttpServer startStub() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.createContext("/api/bookings/7/ticket", exchange -> {
                RECEIVED_HEADERS.set(Map.copyOf(exchange.getRequestHeaders()));
                exchange.getResponseHeaders().add("Location", PRESIGNED);
                exchange.getResponseHeaders().add("Cache-Control", "no-store");
                exchange.sendResponseHeaders(302, -1);
                exchange.close();
            });
            server.createContext("/api/check-ins", exchange -> {
                RECEIVED_HEADERS.set(Map.copyOf(exchange.getRequestHeaders()));
                exchange.getRequestBody().readAllBytes();
                byte[] body = "{\"booking_id\": 7}".getBytes();
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static String sign(String subject, String role, String email) {
        try {
            JWTClaimsSet.Builder claims = new JWTClaimsSet.Builder()
                    .issuer("smart-tours-dev")
                    .audience("smart-tours-api")
                    .subject(subject)
                    .claim("roles", List.of(role)) // the claim SecurityConfig reads roles from
                    .issueTime(new Date())
                    .expirationTime(new Date(System.currentTimeMillis() + 60_000));
            if (email != null) {
                claims.claim("email", email);
            }
            SignedJWT jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims.build());
            jwt.sign(new MACSigner(Base64.getDecoder().decode(SECRET)));
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
