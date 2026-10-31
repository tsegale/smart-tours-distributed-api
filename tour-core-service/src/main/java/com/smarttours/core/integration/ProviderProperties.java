package com.smarttours.core.integration;

import java.net.URI;
import java.time.Duration;

/**
 * Everything that decides how one outbound provider is called: where, how long to wait, how often to
 * retry, when to stop calling it altogether and how many calls may run at once. Values and the reasons
 * for them are in ADR 0012.
 *
 * @param baseUrl        scheme, host and port; tests and the chaos profile point it at WireMock
 * @param connectTimeout TCP connect
 * @param readTimeout    socket read; frees the bulkhead thread, so it must not exceed {@code timeLimit}
 * @param timeLimit      hard bound on one attempt as the caller sees it (Resilience4j TimeLimiter)
 */
public record ProviderProperties(
        URI baseUrl,
        Duration connectTimeout,
        Duration readTimeout,
        Duration timeLimit,
        Retry retry,
        CircuitBreaker circuitBreaker,
        Bulkhead bulkhead) {

    public ProviderProperties {
        if (readTimeout.compareTo(timeLimit) > 0) {
            throw new IllegalArgumentException("readTimeout (" + readTimeout + ") must not exceed timeLimit ("
                    + timeLimit + "): a timed-out caller would leave the bulkhead thread blocked");
        }
    }

    /**
     * Exponential backoff with jitter between attempts, for retryable failures only. A 429's
     * Retry-After replaces the backoff when it is at most {@code maxRetryAfter}; a longer one is
     * not retried at all (the caller falls back instead of waiting).
     */
    public record Retry(int maxAttempts, Duration initialBackoff, double multiplier, double jitter,
                        Duration maxBackoff, Duration maxRetryAfter) {
    }

    /** Count-based sliding window. Slow calls count like failures once over the slow-call rate. */
    public record CircuitBreaker(int slidingWindowSize, int minimumCalls, float failureRatePercent,
                                 Duration slowCallThreshold, float slowCallRatePercent,
                                 Duration waitInOpenState, int permittedCallsInHalfOpenState) {
    }

    /** A thread pool per provider: a slow provider can occupy these threads, never the request threads. */
    public record Bulkhead(int maxThreads, int queueCapacity) {
    }
}
