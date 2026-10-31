package com.smarttours.core.integration;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.bulkhead.ThreadPoolBulkhead;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.time.Duration;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeoutException;

import static org.assertj.core.api.Assertions.assertThat;

class FailuresTest {

    private static final Duration MAX_RETRY_AFTER = Duration.ofSeconds(2);

    @Test
    void transientFailuresAreRetried() {
        assertThat(Failures.isRetryable(new TimeoutException(), MAX_RETRY_AFTER)).isTrue();
        assertThat(Failures.isRetryable(new ResourceAccessException("reset", new IOException("Connection reset")),
                MAX_RETRY_AFTER)).isTrue();
        assertThat(Failures.isRetryable(http(500, null), MAX_RETRY_AFTER)).isTrue();
        assertThat(Failures.isRetryable(http(503, Duration.ofSeconds(1)), MAX_RETRY_AFTER)).isTrue();
        assertThat(Failures.isRetryable(http(429, null), MAX_RETRY_AFTER)).isTrue();
        assertThat(Failures.isRetryable(http(429, Duration.ofSeconds(2)), MAX_RETRY_AFTER)).isTrue();
        // Wrapped the way CompletionStage pipelines deliver them
        assertThat(Failures.isRetryable(new CompletionException(http(502, null)), MAX_RETRY_AFTER)).isTrue();
    }

    @Test
    void permanentFailuresAndBackOffSignalsAreNot() {
        assertThat(Failures.isRetryable(http(400, null), MAX_RETRY_AFTER)).isFalse();
        assertThat(Failures.isRetryable(http(404, null), MAX_RETRY_AFTER)).isFalse();
        assertThat(Failures.isRetryable(http(429, Duration.ofSeconds(3)), MAX_RETRY_AFTER)).isFalse();
        assertThat(Failures.isRetryable(new RestClientException("Error while extracting response"), MAX_RETRY_AFTER))
                .isFalse();
        assertThat(Failures.isRetryable(CallNotPermittedException.createCallNotPermittedException(
                CircuitBreaker.ofDefaults("x")), MAX_RETRY_AFTER)).isFalse();
        assertThat(Failures.isRetryable(BulkheadFullException.createBulkheadFullException(
                ThreadPoolBulkhead.ofDefaults("x")), MAX_RETRY_AFTER)).isFalse();
    }

    @Test
    void onlyProviderTroubleCountsAgainstTheProvider() {
        assertThat(Failures.countsAgainstProvider(new TimeoutException())).isTrue();
        assertThat(Failures.countsAgainstProvider(http(500, null))).isTrue();
        assertThat(Failures.countsAgainstProvider(http(429, null))).isTrue();
        assertThat(Failures.countsAgainstProvider(new RestClientException("malformed"))).isTrue();
        assertThat(Failures.countsAgainstProvider(http(400, null))).isFalse();
        assertThat(Failures.countsAgainstProvider(new CompletionException(BulkheadFullException
                .createBulkheadFullException(ThreadPoolBulkhead.ofDefaults("x"))))).isFalse();
    }

    @Test
    void retryAfterIsReadAsSecondsOrAsAnHttpDate() {
        ZonedDateTime now = ZonedDateTime.of(2026, 10, 31, 10, 0, 0, 0, ZoneOffset.UTC);

        assertThat(Failures.parseRetryAfter("7", now)).isEqualTo(Duration.ofSeconds(7));
        assertThat(Failures.parseRetryAfter(" 0 ", now)).isEqualTo(Duration.ZERO);
        assertThat(Failures.parseRetryAfter("Sat, 31 Oct 2026 10:00:30 GMT", now)).isEqualTo(Duration.ofSeconds(30));
        assertThat(Failures.parseRetryAfter("Sat, 31 Oct 2026 09:59:00 GMT", now)).isEqualTo(Duration.ZERO);
        assertThat(Failures.parseRetryAfter("-5", now)).isNull();
        assertThat(Failures.parseRetryAfter("soon", now)).isNull();
        assertThat(Failures.parseRetryAfter(null, now)).isNull();
    }

    private static ProviderHttpException http(int status, Duration retryAfter) {
        return new ProviderHttpException("test", status, retryAfter, null);
    }
}
