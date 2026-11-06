package com.smarttours.gateway.ratelimit;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.io.IOException;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Iterator;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Server-Sent Events through the real gateway and its real rate limiter: each event has to reach the client while the
 * upstream is still working, or a traveller watches nothing for a minute and then gets it all at once.
 *
 * The upstream won't send its last event until the test has read the first, so buffering anywhere would hang this test
 * rather than slow it. The global response timeout is set far below the gap between events, as an LLM call would make it.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=" + RateLimitTestSupport.SECRET,
        "smarttours.security.dev-tokens.enabled=true",
        "spring.cloud.gateway.server.webflux.httpclient.response-timeout=300ms",
        "RATE_LIMIT_ITINERARY_RPS=1", "RATE_LIMIT_ITINERARY_COST=1", "RATE_LIMIT_ITINERARY_BURST=3"
})
@Testcontainers(disabledWithoutDocker = true)
class ItineraryStreamingTest {

    private static final GenericContainer<?> REDIS =
            new GenericContainer<>(DockerImageName.parse("redis:7.4-alpine")).withExposedPorts(6379);
    private static final CountDownLatch FIRST_EVENT_READ = new CountDownLatch(1);
    private static final HttpServer UPSTREAM = streamingUpstream();

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
        FIRST_EVENT_READ.countDown();
        UPSTREAM.stop(0);
    }

    @Value("${local.server.port}")
    private int port;

    private final HttpClient http = HttpClient.newHttpClient();

    private HttpRequest itinerary(String user) {
        return HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/ai/itineraries"))
                .header("Authorization", RateLimitTestSupport.bearer(user))
                .header("Accept", "text/event-stream")
                .header("Content-Type", "application/json")
                .timeout(Duration.ofSeconds(20))
                .POST(HttpRequest.BodyPublishers.ofString("{\"prompt\":\"dunes\"}"))
                .build();
    }

    @Test
    void eventsArriveWhileTheUpstreamIsStillWorking() throws Exception {
        HttpResponse<Stream<String>> response =
                http.send(itinerary("stream-" + UUID.randomUUID()), HttpResponse.BodyHandlers.ofLines());

        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(response.headers().firstValue("Content-Type").orElse("")).startsWith("text/event-stream");
        try (Stream<String> lines = response.body()) {
            Iterator<String> reader = lines.iterator();
            assertThat(reader.next()).isEqualTo("event: progress");
            assertThat(reader.next()).contains("parsing");
            FIRST_EVENT_READ.countDown();

            StringBuilder rest = new StringBuilder();
            reader.forEachRemaining(line -> rest.append(line).append('\n'));
            assertThat(rest).contains("event: plan");
        }
    }

    @Test
    void theLimitIsTightAndPerUser() throws Exception {
        FIRST_EVENT_READ.countDown();
        String user = "limited-" + UUID.randomUUID();
        // All at once: each served request takes about as long as the bucket takes to refill.
        List<CompletableFuture<HttpResponse<Void>>> burst = IntStream.range(0, 10)
                .mapToObj(i -> http.sendAsync(itinerary(user), HttpResponse.BodyHandlers.discarding()))
                .toList();
        long limited = burst.stream().map(CompletableFuture::join).filter(r -> r.statusCode() == 429).count();
        assertThat(limited).isGreaterThanOrEqualTo(5);
        assertThat(http.send(itinerary("other-" + UUID.randomUUID()), HttpResponse.BodyHandlers.discarding())
                .statusCode()).isEqualTo(200);
    }

    private static HttpServer streamingUpstream() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.setExecutor(Executors.newCachedThreadPool());
            server.createContext("/api/ai/itineraries", exchange -> {
                exchange.getRequestBody().readAllBytes();
                exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
                exchange.sendResponseHeaders(200, 0);
                try (OutputStream body = exchange.getResponseBody()) {
                    send(body, "event: progress\ndata: {\"stage\":\"parsing\"}\n\n");
                    // Longer than the global response timeout, as a call to a model would be.
                    assertRead();
                    Thread.sleep(800);
                    send(body, "event: plan\ndata: {\"path\":\"deterministic\"}\n\n");
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static void assertRead() throws InterruptedException {
        if (!FIRST_EVENT_READ.await(10, TimeUnit.SECONDS)) {
            throw new IllegalStateException("The client never saw the first event: something buffered the stream");
        }
    }

    private static void send(OutputStream body, String event) throws IOException {
        body.write(event.getBytes(StandardCharsets.UTF_8));
        body.flush();
    }
}
