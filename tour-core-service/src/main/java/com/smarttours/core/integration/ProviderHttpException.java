package com.smarttours.core.integration;

import java.time.Duration;

/** A provider answered with an HTTP error status. {@code retryAfter} is set when it sent a usable Retry-After. */
public class ProviderHttpException extends RuntimeException {

    private final String provider;
    private final int status;
    private final Duration retryAfter;

    public ProviderHttpException(String provider, int status, Duration retryAfter, String body) {
        super(provider + " answered " + status + (body == null || body.isBlank() ? "" : ": " + body));
        this.provider = provider;
        this.status = status;
        this.retryAfter = retryAfter;
    }

    public String getProvider() {
        return provider;
    }

    public int getStatus() {
        return status;
    }

    public Duration getRetryAfter() {
        return retryAfter;
    }

    public boolean isServerError() {
        return status >= 500;
    }

    public boolean isTooManyRequests() {
        return status == 429;
    }
}
