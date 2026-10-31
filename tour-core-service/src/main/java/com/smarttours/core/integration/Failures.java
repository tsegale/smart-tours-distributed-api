package com.smarttours.core.integration;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import org.springframework.web.client.ResourceAccessException;

import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;

/**
 * How an outbound failure is treated (ADR 0012). Only idempotent GETs go through these policies, so
 * "retryable" is decided by the failure alone:
 * <ul>
 *   <li><b>retried</b>: attempt timeouts, I/O errors (refused, reset, read timeout), 5xx, and 429 when
 *       its Retry-After is short enough to wait for;</li>
 *   <li><b>not retried</b>: other 4xx (our request is wrong; asking again won't help), unreadable bodies
 *       (the provider changed its format), an open circuit and a full bulkhead (both mean "back off").</li>
 * </ul>
 */
public final class Failures {

    private Failures() {
    }

    /** CompletionStage pipelines wrap the real failure; every decision is made on the cause. */
    public static Throwable unwrap(Throwable failure) {
        Throwable current = failure;
        while ((current instanceof CompletionException || current instanceof ExecutionException)
                && current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    public static boolean isRetryable(Throwable failure, Duration maxRetryAfter) {
        Throwable cause = unwrap(failure);
        if (cause instanceof TimeoutException || cause instanceof ResourceAccessException) {
            return true;
        }
        if (cause instanceof ProviderHttpException http) {
            if (http.isTooManyRequests()) {
                return http.getRetryAfter() == null || http.getRetryAfter().compareTo(maxRetryAfter) <= 0;
            }
            return http.isServerError();
        }
        return false;
    }

    /**
     * Whether a failure says something about the provider's health, for the circuit breaker. A 4xx
     * other than 429 is our mistake, and a full bulkhead is our own capacity; neither should open it.
     */
    public static boolean countsAgainstProvider(Throwable failure) {
        Throwable cause = unwrap(failure);
        if (cause instanceof BulkheadFullException || cause instanceof CallNotPermittedException) {
            return false;
        }
        if (cause instanceof ProviderHttpException http) {
            return http.isServerError() || http.isTooManyRequests();
        }
        return true;
    }

    /** The provider's Retry-After, if the failure is a 429 or 503 that carried one. */
    public static Duration retryAfter(Throwable failure) {
        return unwrap(failure) instanceof ProviderHttpException http ? http.getRetryAfter() : null;
    }

    /** RFC 9110 Retry-After: delay-seconds or an HTTP-date. Null when absent or unreadable. */
    public static Duration parseRetryAfter(String value, ZonedDateTime now) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String trimmed = value.trim();
        try {
            long seconds = Long.parseLong(trimmed);
            return seconds < 0 ? null : Duration.ofSeconds(seconds);
        } catch (NumberFormatException notSeconds) {
            try {
                Duration until = Duration.between(now, ZonedDateTime.parse(trimmed, DateTimeFormatter.RFC_1123_DATE_TIME));
                return until.isNegative() ? Duration.ZERO : until;
            } catch (DateTimeParseException unreadable) {
                return null;
            }
        }
    }
}
