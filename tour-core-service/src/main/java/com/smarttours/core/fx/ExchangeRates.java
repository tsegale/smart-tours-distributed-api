package com.smarttours.core.fx;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.time.Clock;
import java.time.Instant;
import java.util.Optional;
import java.util.concurrent.locks.ReentrantLock;

/**
 * The rates in use, for the request path. Never calls a provider. Three tiers:
 * <ol>
 *   <li><b>in-process</b>: the hot path; re-read every {@code local-ttl} (default 60 s), so a refresh
 *       done by another instance reaches this one within that time;</li>
 *   <li><b>Redis</b> ({@code fx:latest:NAD}): shared by all instances, written by whichever ran the refresh;</li>
 *   <li><b>the database</b>: the source of truth; read when Redis is empty or down, then written back to Redis.</li>
 * </ol>
 * Only one thread per instance reloads; the others keep using the copy they have meanwhile. A failed
 * reload keeps the old copy too, so neither a Redis nor a database outage takes away rates already loaded.
 */
@Component
public class ExchangeRates {

    private static final Logger log = LoggerFactory.getLogger(ExchangeRates.class);
    static final String REDIS_KEY_PREFIX = "fx:latest:";

    private record Local(RateSnapshot snapshot, Instant loadedAt) {
    }

    private final FxRateRepository repository;
    private final StringRedisTemplate redis;
    private final JsonMapper json;
    private final FxProperties properties;
    private final Clock clock;
    private final MeterRegistry meters;
    private final ReentrantLock reloading = new ReentrantLock();
    private volatile Local local;

    public ExchangeRates(FxRateRepository repository, StringRedisTemplate redis, JsonMapper json,
                         FxProperties properties, Clock clock, MeterRegistry meters) {
        this.repository = repository;
        this.redis = redis;
        this.json = json;
        this.properties = properties;
        this.clock = clock;
        this.meters = meters;
    }

    public Optional<RateSnapshot> current() {
        Local copy = local;
        if (copy != null && isFresh(copy)) {
            return Optional.ofNullable(copy.snapshot());
        }
        if (copy != null && !reloading.tryLock()) {
            return Optional.ofNullable(copy.snapshot()); // someone else is reloading; this copy will do meanwhile
        }
        if (copy == null) {
            reloading.lock(); // nothing to serve yet: wait for the first load
        }
        try {
            copy = local;
            if (copy != null && isFresh(copy)) {
                return Optional.ofNullable(copy.snapshot());
            }
            RateSnapshot loaded = load(copy == null ? null : copy.snapshot());
            local = new Local(loaded, clock.instant());
            return Optional.ofNullable(loaded);
        } finally {
            reloading.unlock();
        }
    }

    /** A refresh was accepted: use it here at once, and share it through Redis. */
    public void publish(RateSnapshot snapshot) {
        local = new Local(snapshot, clock.instant());
        writeRedis(snapshot);
    }

    /** Forget the in-process copy (tests, and after a manual change in the database). */
    public void evictLocal() {
        local = null;
    }

    private boolean isFresh(Local copy) {
        return copy.loadedAt().plus(properties.localTtl()).isAfter(clock.instant());
    }

    private RateSnapshot load(RateSnapshot previous) {
        Optional<RateSnapshot> fromRedis = readRedis();
        if (fromRedis.isPresent()) {
            count("redis");
            return fromRedis.get();
        }
        try {
            Optional<RateSnapshot> fromDatabase = repository.latest(properties.base());
            count("database");
            fromDatabase.ifPresent(this::writeRedis);
            return fromDatabase.orElse(null);
        } catch (RuntimeException ex) {
            log.warn("Could not load exchange rates from the database, keeping the copy in memory: {}", ex.toString());
            count("failed");
            return previous;
        }
    }

    private Optional<RateSnapshot> readRedis() {
        try {
            String value = redis.opsForValue().get(REDIS_KEY_PREFIX + properties.base());
            return value == null ? Optional.empty() : Optional.of(json.readValue(value, RateSnapshot.class));
        } catch (RuntimeException ex) {
            cacheError("get", ex);
            return Optional.empty();
        }
    }

    private void writeRedis(RateSnapshot snapshot) {
        try {
            redis.opsForValue().set(REDIS_KEY_PREFIX + snapshot.base(), json.writeValueAsString(snapshot));
        } catch (RuntimeException ex) {
            cacheError("put", ex);
        }
    }

    private void count(String source) {
        Counter.builder("fx.snapshot.loads")
                .description("Reloads of the in-process exchange-rate copy, by where they came from")
                .tag("source", source)
                .register(meters)
                .increment();
    }

    /** Same meter as the catalogue cache (ADR 0009), so one alert covers every Redis fall-through. */
    private void cacheError(String operation, RuntimeException ex) {
        Counter.builder("cache.errors")
                .description("Cache operations that failed and fell through to the database")
                .tag("cache", "fx")
                .tag("operation", operation)
                .register(meters)
                .increment();
        log.warn("Exchange-rate cache {} failed, falling through: {}", operation, ex.toString());
    }
}
