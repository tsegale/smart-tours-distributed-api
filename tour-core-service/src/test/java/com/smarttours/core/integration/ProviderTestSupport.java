package com.smarttours.core.integration;

import io.github.resilience4j.bulkhead.ThreadPoolBulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.retry.RetryRegistry;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;

/**
 * Builds the real adapters against WireMock without a Spring context: the same {@link OutboundClients} and
 * {@link ProviderPolicies}, with small numbers so failure modes show up in milliseconds.
 */
public final class ProviderTestSupport {

    public static final String USER_AGENT = "SmartTours-Test/1.0";

    private ProviderTestSupport() {
    }

    /** One attempt per call takes at most {@code timeLimit}; the circuit opens after {@code minimumCalls} failures. */
    public static ProviderProperties provider(String baseUrl, Duration timeLimit, int maxAttempts, int minimumCalls,
                                              Duration waitInOpenState, int threads, int queue) {
        return new ProviderProperties(URI.create(baseUrl), Duration.ofMillis(500), timeLimit, timeLimit,
                new ProviderProperties.Retry(maxAttempts, Duration.ofMillis(20), 2, 0.5, Duration.ofMillis(100),
                        Duration.ofSeconds(2)),
                new ProviderProperties.CircuitBreaker(minimumCalls, minimumCalls, 50, timeLimit, 100,
                        waitInOpenState, 1),
                new ProviderProperties.Bulkhead(threads, queue));
    }

    /**
     * Generous defaults: 2 attempts of 1 s, a breaker that needs 10 calls, 4 threads. A second, not less: the
     * first call in a fresh JVM loads the HTTP and JSON stacks and took over 3 s on a laptop.
     */
    public static ProviderProperties provider(String baseUrl) {
        return provider(baseUrl, Duration.ofSeconds(1), 2, 10, Duration.ofSeconds(30), 4, 8);
    }

    /**
     * For a test class's one warm-up call only: 30 s and 3 attempts, so loading the stacks on a slow or loaded
     * machine can't fail the warm-up that exists to keep that cost out of the tests' time limits.
     */
    public static ProviderProperties warmUp(String baseUrl) {
        return provider(baseUrl, Duration.ofSeconds(30), 3, 10, Duration.ofSeconds(30), 4, 8);
    }

    public static IntegrationProperties integrations(ProviderProperties frankfurter, ProviderProperties forecast,
                                                     ProviderProperties archive) {
        return new IntegrationProperties(USER_AGENT, frankfurter, forecast, archive);
    }

    public static OutboundClients clients(IntegrationProperties properties) {
        return new OutboundClients(RestClient.builder(), properties, Clock.systemUTC());
    }

    public static ProviderPolicies policies() {
        return new ProviderPolicies(CircuitBreakerRegistry.ofDefaults(), RetryRegistry.ofDefaults(),
                TimeLimiterRegistry.ofDefaults(), ThreadPoolBulkheadRegistry.ofDefaults());
    }

    /** A recorded real response from {@code src/test/resources/fixtures}. */
    public static String fixture(String path) {
        try (InputStream in = ProviderTestSupport.class.getResourceAsStream("/fixtures/" + path)) {
            if (in == null) {
                throw new IllegalArgumentException("no fixture " + path);
            }
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }
}
