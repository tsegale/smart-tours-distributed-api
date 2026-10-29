package com.smarttours.gateway;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The public webhook route end to end, through the real gateway on a random port, to a stub
 * booking-service (a JDK HttpServer) that records exactly what it received.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=qS0t8yQ0bX9mJ3vV6cP2nL5kR8wE1zT4uY7iO0pA3sD6fG9hJ2kL5zX8cV1bN4mQ",
        "TOUR_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
class WebhookRouteTest {

    private static final HttpServer BOOKING_SERVICE = startStub();
    private static final AtomicReference<byte[]> RECEIVED_BODY = new AtomicReference<>();
    private static final AtomicReference<Map<String, List<String>>> RECEIVED_HEADERS = new AtomicReference<>();

    // Whitespace and key order a re-serialising proxy would change, which would break the signature.
    private static final String RAW_EVENT = "{\n  \"id\": \"evt_1\",   \"type\":\"checkout.session.completed\" ,\n"
            + "  \"data\": {\"object\": {\"amount_total\": 370000.0}}\n}\n";

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
        RECEIVED_BODY.set(null);
        RECEIVED_HEADERS.set(null);
    }

    @AfterAll
    static void stopStub() {
        BOOKING_SERVICE.stop(0);
    }

    @Test
    void forwardsTheRawBodyAndSignatureUnchanged_withoutIdentityHeaders() {
        client.post().uri("/webhooks/stripe")
                .contentType(MediaType.APPLICATION_JSON)
                .header("Stripe-Signature", "t=1700000000,v1=abc123")
                .header("X-User-Id", "mallory")
                .header("X-User-Roles", "ADMIN")
                .bodyValue(RAW_EVENT.getBytes(StandardCharsets.UTF_8))
                .exchange()
                .expectStatus().isOk()
                .expectBody(String.class).isEqualTo("{\"received\":true}");

        assertThat(RECEIVED_BODY.get()).isEqualTo(RAW_EVENT.getBytes(StandardCharsets.UTF_8));
        Map<String, List<String>> headers = RECEIVED_HEADERS.get();
        assertThat(headers.get("Stripe-signature")).containsExactly("t=1700000000,v1=abc123");
        assertThat(headers).doesNotContainKeys("X-user-id", "X-user-roles");
    }

    @Test
    void validTokenOnTheWebhookRoute_stillPropagatesNoIdentity() {
        client.post().uri("/webhooks/stripe")
                .header("Authorization", "Bearer not-even-a-jwt")
                .bodyValue("{}")
                .exchange()
                .expectStatus().isOk();

        assertThat(RECEIVED_HEADERS.get()).doesNotContainKeys("X-user-id", "X-user-roles");
    }

    @Test
    void oversizedBodies_areRefusedAtTheEdge() {
        byte[] huge = new byte[300 * 1024];
        client.post().uri("/webhooks/stripe")
                .header("Content-Length", String.valueOf(huge.length))
                .bodyValue(huge)
                .exchange()
                .expectStatus().isEqualTo(413);

        assertThat(RECEIVED_BODY.get()).isNull();
    }

    private static HttpServer startStub() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.createContext("/webhooks/stripe", exchange -> {
                RECEIVED_BODY.set(exchange.getRequestBody().readAllBytes());
                RECEIVED_HEADERS.set(Map.copyOf(exchange.getRequestHeaders()));
                byte[] response = "{\"received\":true}".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, response.length);
                exchange.getResponseBody().write(response);
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
