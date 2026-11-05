package com.smarttours.gateway.security;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpMethod;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

import static com.smarttours.gateway.security.IssuerTestSupport.DEV_SECRET;
import static org.assertj.core.api.Assertions.assertThat;

/** CORS, security headers and request limits on a real server: what a browser and a raw client would see. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.dev-tokens.enabled=true",
        "smarttours.security.jwt-secret=" + DEV_SECRET,
        "smarttours.security.cors.allowed-origins=https://app.example.test,https://admin.example.test",
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1",
        "spring.data.redis.port=1"
})
class EdgeHardeningTest {

    private static final String ALLOWED = "https://app.example.test";

    @Value("${local.server.port}")
    private int port;

    private WebTestClient client() {
        return WebTestClient.bindToServer().baseUrl("http://localhost:" + port).build();
    }

    @Test
    void aPreflightFromAnAllowedOriginSucceeds() {
        client().options().uri("/api/bookings")
                .header("Origin", ALLOWED)
                .header("Access-Control-Request-Method", "POST")
                .header("Access-Control-Request-Headers", "authorization,content-type,idempotency-key")
                .exchange()
                .expectStatus().isOk()
                .expectHeader().valueEquals("Access-Control-Allow-Origin", ALLOWED)
                .expectHeader().doesNotExist("Access-Control-Allow-Credentials");
    }

    @Test
    void aPreflightFromAnyOtherOriginIsRefused() {
        client().options().uri("/api/bookings")
                .header("Origin", "https://evil.example.test")
                .header("Access-Control-Request-Method", "POST")
                .exchange()
                .expectStatus().isForbidden()
                .expectHeader().doesNotExist("Access-Control-Allow-Origin");
    }

    @Test
    void aPreflightForAMethodNotOnTheListIsRefused() {
        client().options().uri("/api/bookings")
                .header("Origin", ALLOWED)
                .header("Access-Control-Request-Method", "TRACE")
                .exchange()
                .expectStatus().isForbidden();
    }

    @Test
    void responsesCarrySecurityHeaders() {
        client().get().uri("/actuator/health").exchange()
                .expectHeader().valueEquals("X-Content-Type-Options", "nosniff")
                .expectHeader().valueEquals("X-Frame-Options", "DENY")
                .expectHeader().valueEquals("Referrer-Policy", "no-referrer")
                .expectHeader().valueEquals("Content-Security-Policy", SecurityConfig.API_CONTENT_SECURITY_POLICY);
    }

    @Test
    void theApiDocsKeepAPolicySwaggerUiCanRunUnder() {
        client().method(HttpMethod.GET).uri("/docs/tours/swagger-ui.html").exchange()
                .expectHeader().doesNotExist("Content-Security-Policy")
                .expectHeader().valueEquals("X-Content-Type-Options", "nosniff");
    }

    @Test
    void aBodyOverTheDefaultLimitIsRefusedBeforeItReachesAService() {
        byte[] body = new byte[2 * 1024 * 1024];
        client().post().uri("/api/bookings")
                .header("Authorization", "Bearer " + IssuerTestSupport.signHs256(IssuerTestSupport.claims(
                        IssuerTestSupport.DEV_ISSUER, IssuerTestSupport.AUDIENCE, "CUSTOMER")))
                .header("Content-Length", String.valueOf(body.length))
                .bodyValue(body)
                .exchange()
                .expectStatus().isEqualTo(413);
    }

    /** A raw socket: HTTP clients choose their own framing, and this has to be chunked with no length. */
    @Test
    void aChunkedBodyIsRefused_itWouldOtherwisePassEverySizeLimit() throws IOException {
        try (Socket socket = new Socket("localhost", port)) {
            OutputStream out = socket.getOutputStream();
            out.write(("POST /webhooks/stripe HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                    + "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n")
                    .getBytes(StandardCharsets.US_ASCII));
            out.flush();
            InputStream in = socket.getInputStream();
            String response = new String(in.readAllBytes(), StandardCharsets.US_ASCII);
            assertThat(response).startsWith("HTTP/1.1 411");
        }
    }
}
