package com.smarttours.core.weather;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Weather in Redis (the catalogue cache's instance, ADR 0009), stale-if-error style: each entry knows until
 * when it is fresh, but Redis keeps it much longer. Fresh entries are served as they are; an expired one
 * triggers a fetch, and is served marked stale if that fetch fails. Redis down is a miss, never an error.
 */
@Component
public class WeatherCache {

    private static final Logger log = LoggerFactory.getLogger(WeatherCache.class);

    /** What is stored: the report and the moment it stops being fresh. */
    public record Entry(WeatherReport report, Instant freshUntil) {

        public boolean isFresh(Instant now) {
            return now.isBefore(freshUntil);
        }
    }

    private final StringRedisTemplate redis;
    private final JsonMapper json;
    private final MeterRegistry meters;

    public WeatherCache(StringRedisTemplate redis, JsonMapper json, MeterRegistry meters) {
        this.redis = redis;
        this.json = json;
        this.meters = meters;
    }

    public Entry get(String key) {
        try {
            String value = redis.opsForValue().get(key);
            return value == null ? null : json.readValue(value, Entry.class);
        } catch (RuntimeException ex) {
            error("get", key, ex);
            return null;
        }
    }

    /** Fresh for {@code ttl} ± {@code jitter}, kept for {@code retention} (at least the TTL). */
    public void put(String key, WeatherReport report, Instant now, Duration ttl, double jitter, Duration retention) {
        long spread = Math.round(ttl.toMillis() * jitter);
        Duration fresh = ttl.plusMillis(spread == 0 ? 0 : ThreadLocalRandom.current().nextLong(-spread, spread + 1));
        Duration keep = retention.compareTo(fresh) > 0 ? retention : fresh;
        try {
            redis.opsForValue().set(key, json.writeValueAsString(new Entry(report, now.plus(fresh))), keep);
        } catch (RuntimeException ex) {
            error("put", key, ex);
        }
    }

    private void error(String operation, String key, RuntimeException ex) {
        Counter.builder("cache.errors")
                .description("Cache operations that failed and fell through to the database")
                .tag("cache", "weather")
                .tag("operation", operation)
                .register(meters)
                .increment();
        log.warn("Weather cache {} failed for key {}, falling through: {}", operation, key, ex.toString());
    }
}
