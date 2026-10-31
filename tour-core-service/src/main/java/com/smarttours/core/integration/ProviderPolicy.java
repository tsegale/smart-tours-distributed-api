package com.smarttours.core.integration;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.bulkhead.ThreadPoolBulkhead;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.timelimiter.TimeLimiter;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ScheduledExecutorService;
import java.util.function.Supplier;

/**
 * The resilience policy of one provider, applied to every call to it, outermost first:
 * <pre>
 *   Retry( CircuitBreaker( TimeLimiter( ThreadPoolBulkhead( call ) ) ) )
 * </pre>
 * Resilience4j's own recommended order. Each retry attempt passes the breaker again, so an open circuit
 * stops the retries at once; the time limit applies per attempt; and the call itself runs on the
 * provider's own threads, so a provider that hangs ties up those threads and never the caller's.
 *
 * <p>When the time limit fires, the caller gets a {@link java.util.concurrent.TimeoutException} at once.
 * The bulkhead thread is released by the HTTP read timeout, which is why that must not exceed the limit.
 */
public final class ProviderPolicy {

    private final String name;
    private final Retry retry;
    private final CircuitBreaker circuitBreaker;
    private final TimeLimiter timeLimiter;
    private final ThreadPoolBulkhead bulkhead;
    private final ScheduledExecutorService scheduler;

    public ProviderPolicy(String name, Retry retry, CircuitBreaker circuitBreaker, TimeLimiter timeLimiter,
                          ThreadPoolBulkhead bulkhead, ScheduledExecutorService scheduler) {
        this.name = name;
        this.retry = retry;
        this.circuitBreaker = circuitBreaker;
        this.timeLimiter = timeLimiter;
        this.bulkhead = bulkhead;
        this.scheduler = scheduler;
    }

    public <T> CompletableFuture<T> call(Supplier<T> call) {
        Supplier<CompletionStage<T>> isolated = () -> submit(call);
        Supplier<CompletionStage<T>> limited = timeLimiter.decorateCompletionStage(scheduler, isolated);
        Supplier<CompletionStage<T>> guarded = CircuitBreaker.decorateCompletionStage(circuitBreaker, limited);
        Supplier<CompletionStage<T>> retried = Retry.decorateCompletionStage(retry, scheduler, guarded);
        return retried.get().toCompletableFuture();
    }

    /** A full bulkhead is reported as a failed attempt, like every other failure, not thrown at the caller. */
    private <T> CompletionStage<T> submit(Supplier<T> call) {
        try {
            return bulkhead.submit(call::get);
        } catch (BulkheadFullException full) {
            return CompletableFuture.failedFuture(full);
        }
    }

    public String name() {
        return name;
    }

    public CircuitBreaker circuitBreaker() {
        return circuitBreaker;
    }

    public Retry retry() {
        return retry;
    }

    public ThreadPoolBulkhead bulkhead() {
        return bulkhead;
    }
}
