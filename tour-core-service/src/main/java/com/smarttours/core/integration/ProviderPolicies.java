package com.smarttours.core.integration;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.bulkhead.ThreadPoolBulkhead;
import io.github.resilience4j.bulkhead.ThreadPoolBulkheadConfig;
import io.github.resilience4j.bulkhead.ThreadPoolBulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.retry.RetryRegistry;
import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Builds a {@link ProviderPolicy} from {@link ProviderProperties}. The instances live in the registries
 * that resilience4j-spring-boot4 auto-configures, which is what binds their metrics to Micrometer
 * ({@code resilience4j_circuitbreaker_state}, {@code resilience4j_retry_calls_total},
 * {@code resilience4j_bulkhead_*}, {@code resilience4j_timelimiter_calls_total}, tagged by provider name)
 * and exposes them on {@code /actuator/prometheus}.
 *
 * <p>The configs are built here rather than in {@code resilience4j.*} YAML because two of the rules are
 * code (which failures are retried and counted, and Retry-After), and keeping every number for a provider
 * in one {@code smarttours.integrations.*} block next to its timeouts makes the policy readable in one place.
 */
@Component
public class ProviderPolicies implements DisposableBean {

    private static final Logger log = LoggerFactory.getLogger(ProviderPolicies.class);

    private final CircuitBreakerRegistry circuitBreakers;
    private final RetryRegistry retries;
    private final TimeLimiterRegistry timeLimiters;
    private final ThreadPoolBulkheadRegistry bulkheads;
    private final ScheduledExecutorService scheduler;

    public ProviderPolicies(CircuitBreakerRegistry circuitBreakers, RetryRegistry retries,
                            TimeLimiterRegistry timeLimiters, ThreadPoolBulkheadRegistry bulkheads) {
        this.circuitBreakers = circuitBreakers;
        this.retries = retries;
        this.timeLimiters = timeLimiters;
        this.bulkheads = bulkheads;
        this.scheduler = newScheduler();
    }

    /**
     * Fires time limits and schedules retry waits for every provider. It only schedules (the calls run on
     * each provider's bulkhead), so two threads are plenty. Deliberately not a bean: a ScheduledExecutorService
     * bean makes Spring Boot skip its own task scheduler and run every @Scheduled method on it.
     */
    private static ScheduledExecutorService newScheduler() {
        AtomicInteger counter = new AtomicInteger();
        return Executors.newScheduledThreadPool(2, runnable -> {
            Thread thread = new Thread(runnable, "resilience-" + counter.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        });
    }

    @Override
    public void destroy() {
        scheduler.shutdownNow();
    }

    public ProviderPolicy create(String name, ProviderProperties provider) {
        CircuitBreaker circuitBreaker = circuitBreakers.circuitBreaker(name, circuitBreakerConfig(provider.circuitBreaker()));
        circuitBreaker.getEventPublisher().onStateTransition(event ->
                log.warn("Circuit breaker {}: {}", name, event.getStateTransition()));
        return new ProviderPolicy(
                name,
                retries.retry(name, retryConfig(provider.retry())),
                circuitBreaker,
                timeLimiters.timeLimiter(name, TimeLimiterConfig.custom()
                        .timeoutDuration(provider.timeLimit())
                        .cancelRunningFuture(true)
                        .build()),
                bulkheads.bulkhead(name, bulkheadConfig(provider.bulkhead())),
                scheduler);
    }

    static RetryConfig retryConfig(ProviderProperties.Retry retry) {
        IntervalFunction backoff = IntervalFunction.ofExponentialRandomBackoff(
                retry.initialBackoff(), retry.multiplier(), retry.jitter(), retry.maxBackoff());
        return RetryConfig.custom()
                .maxAttempts(retry.maxAttempts())
                .retryOnException(failure -> Failures.isRetryable(failure, retry.maxRetryAfter()))
                // Retry-After, when the provider sent one we are willing to wait for, beats our own backoff.
                .intervalBiFunction((attempt, outcome) -> {
                    Duration retryAfter = outcome.isLeft() ? Failures.retryAfter(outcome.getLeft()) : null;
                    return retryAfter != null ? retryAfter.toMillis() : backoff.apply(attempt);
                })
                .failAfterMaxAttempts(false)
                .build();
    }

    static CircuitBreakerConfig circuitBreakerConfig(ProviderProperties.CircuitBreaker breaker) {
        return CircuitBreakerConfig.custom()
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(breaker.slidingWindowSize())
                .minimumNumberOfCalls(breaker.minimumCalls())
                .failureRateThreshold(breaker.failureRatePercent())
                .slowCallDurationThreshold(breaker.slowCallThreshold())
                .slowCallRateThreshold(breaker.slowCallRatePercent())
                .waitDurationInOpenState(breaker.waitInOpenState())
                .permittedNumberOfCallsInHalfOpenState(breaker.permittedCallsInHalfOpenState())
                .automaticTransitionFromOpenToHalfOpenEnabled(true)
                .recordException(Failures::countsAgainstProvider)
                .ignoreExceptions(BulkheadFullException.class)
                .build();
    }

    static ThreadPoolBulkheadConfig bulkheadConfig(ProviderProperties.Bulkhead bulkhead) {
        return ThreadPoolBulkheadConfig.custom()
                .coreThreadPoolSize(bulkhead.maxThreads())
                .maxThreadPoolSize(bulkhead.maxThreads())
                .queueCapacity(bulkhead.queueCapacity())
                .build();
    }
}
