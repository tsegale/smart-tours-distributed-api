package com.smarttours.gateway;

import com.smarttours.gateway.ratelimit.RateLimitTestSupport;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * A service that is stopped or dies answers Problem Details saying so, with no internal detail in it. It used to be Boot's
 * generic 500, first seen when the reranker got the AI service killed for memory.
 *
 * The upstream is a raw socket, because an HTTP server won't send a response cut short on purpose.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=" + TicketRouteTest.SECRET,
        "smarttours.security.dev-tokens.enabled=true",
        "spring.cloud.gateway.server.webflux.httpclient.response-timeout=500ms",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1",
        "spring.data.redis.port=1"
})
class UpstreamFailureTest {

    private static final ServerSocket UPSTREAM = startUpstream();

    @Value("${local.server.port}")
    private int port;

    @DynamicPropertySource
    static void wiring(DynamicPropertyRegistry registry) {
        registry.add("TOUR_SERVICE_URL", () -> "http://localhost:" + UPSTREAM.getLocalPort());
    }

    @AfterAll
    static void stop() throws IOException {
        UPSTREAM.close();
    }

    private WebTestClient client() {
        return WebTestClient.bindToServer().baseUrl("http://localhost:" + port)
                .responseTimeout(Duration.ofSeconds(20)).build();
    }

    private void expectProblem(String path, int status, String title) {
        byte[] body = client().get().uri(path).header("Authorization", RateLimitTestSupport.bearer("upstream-failure"))
                .exchange()
                .expectStatus().isEqualTo(status)
                .expectHeader().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON)
                .expectBody()
                .jsonPath("$.type").isEqualTo("about:blank")
                .jsonPath("$.title").isEqualTo(title)
                .jsonPath("$.status").isEqualTo(status)
                .jsonPath("$.detail").isNotEmpty()
                .jsonPath("$.requestId").isNotEmpty()
                .returnResult().getResponseBody();
        assertThat(new String(body, StandardCharsets.UTF_8))
                .doesNotContain("localhost", "Exception", "timestamp");
    }

    @Test
    void aServiceRefusingConnectionsIsA502() {
        expectProblem("/api/availability?tour_id=1", 502, "Bad Gateway");
    }

    @Test
    void aServiceDyingBeforeItAnswersIsA502() {
        expectProblem("/api/tours/die-before-response", 502, "Bad Gateway");
    }

    @Test
    void aHungServiceIsA504() {
        expectProblem("/api/tours/hang", 504, "Gateway Timeout");
    }

    @Test
    void anUnknownPathIsA404Problem() {
        client().get().uri("/no-such-route").header("Authorization", RateLimitTestSupport.bearer("upstream-failure"))
                .exchange()
                .expectStatus().isEqualTo(404)
                .expectHeader().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON)
                .expectBody().jsonPath("$.status").isEqualTo(404);
    }

    @Test
    void aServiceDyingMidResponseCutsTheConnectionAndAppendsNothing() {
        HttpRequest request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/tours/die-mid-response"))
                .timeout(Duration.ofSeconds(20)).build();
        // Short of the promised Content-Length: the client must see a failure, never a complete-looking body.
        assertThatThrownBy(() -> HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString()))
                .isInstanceOf(IOException.class);
    }

    private static ServerSocket startUpstream() {
        try {
            ServerSocket server = new ServerSocket(0);
            Thread.ofVirtual().start(() -> {
                while (!server.isClosed()) {
                    try {
                        Socket socket = server.accept();
                        Thread.ofVirtual().start(() -> serve(socket));
                    } catch (IOException closing) {
                        return;
                    }
                }
            });
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static void serve(Socket socket) {
        try (socket) {
            String head = readHead(socket.getInputStream());
            if (head.contains("/hang")) {
                Thread.sleep(5_000);
            } else if (head.contains("/die-mid-response")) {
                OutputStream out = socket.getOutputStream();
                out.write("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1000\r\n\r\npartial"
                        .getBytes(StandardCharsets.UTF_8));
                out.flush();
                Thread.sleep(200);
            }
        } catch (IOException gatewayHungUp) {
            // The gateway closing its side is what the hang test expects.
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static String readHead(InputStream in) throws IOException {
        StringBuilder head = new StringBuilder();
        int b;
        while (head.indexOf("\r\n\r\n") < 0 && (b = in.read()) != -1) {
            head.append((char) b);
        }
        return head.toString();
    }
}
