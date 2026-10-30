package com.smarttours.core.cache;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.Cache;
import org.springframework.cache.interceptor.CacheErrorHandler;

/**
 * A cache failure is logged, counted and otherwise ignored: a failed get is a miss, a failed put or
 * evict is skipped. The request carries on against the database, so a Redis outage costs latency,
 * not availability. {@code cache.errors} in /actuator/prometheus shows when that is happening.
 *
 * <p>An evict that fails leaves the old entry until its TTL ends; that bounded staleness is the
 * documented price of failing open (ADR 0009).
 */
public class FallThroughCacheErrorHandler implements CacheErrorHandler {

    private static final Logger log = LoggerFactory.getLogger(FallThroughCacheErrorHandler.class);

    private final MeterRegistry meterRegistry;

    public FallThroughCacheErrorHandler(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    @Override
    public void handleCacheGetError(RuntimeException exception, Cache cache, Object key) {
        record("get", cache, key, exception);
    }

    @Override
    public void handleCachePutError(RuntimeException exception, Cache cache, Object key, Object value) {
        record("put", cache, key, exception);
    }

    @Override
    public void handleCacheEvictError(RuntimeException exception, Cache cache, Object key) {
        record("evict", cache, key, exception);
    }

    @Override
    public void handleCacheClearError(RuntimeException exception, Cache cache) {
        record("clear", cache, null, exception);
    }

    void record(String operation, Cache cache, Object key, RuntimeException exception) {
        String name = cache == null ? "none" : cache.getName();
        Counter.builder("cache.errors")
                .description("Cache operations that failed and fell through to the database")
                .tag("cache", name)
                .tag("operation", operation)
                .register(meterRegistry)
                .increment();
        // No stack trace: during an outage this fires on every request, and the cause is in the message.
        log.warn("Cache {} on {} failed for key {}, falling through: {}", operation, name, key, exception.toString());
    }
}
