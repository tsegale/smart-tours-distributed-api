package com.smarttours.gateway;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;

/** A hung service answers 504 within the configured time: nothing used to bound it. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "smarttours.security.jwt-secret=" + TicketRouteTest.SECRET,
        "smarttours.security.dev-tokens.enabled=true",
        "spring.cloud.gateway.server.webflux.httpclient.response-timeout=500ms",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1",
        "spring.data.redis.port=1"
})
class UpstreamTimeoutTest {

    private static final CountDownLatch RELEASE = new CountDownLatch(1);
    private static final HttpServer HUNG = startHung();

    @Value("${local.server.port}")
    private int port;

    @DynamicPropertySource
    static void hungService(DynamicPropertyRegistry registry) {
        registry.add("TOUR_SERVICE_URL", () -> "http://localhost:" + HUNG.getAddress().getPort());
    }

    @AfterAll
    static void stop() {
        RELEASE.countDown();
        HUNG.stop(0);
    }

    @Test
    void aHungServiceGetsA504() {
        WebTestClient.bindToServer().baseUrl("http://localhost:" + port).build()
                .get().uri("/api/tours/1").exchange().expectStatus().isEqualTo(504);
    }

    private static HttpServer startHung() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.setExecutor(Executors.newCachedThreadPool());
            server.createContext("/", exchange -> {
                try {
                    RELEASE.await();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
