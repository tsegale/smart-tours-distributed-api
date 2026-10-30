package com.smarttours.core.cache;

import org.springframework.data.redis.cache.RedisCacheWriter;

import java.time.Duration;
import java.util.concurrent.ThreadLocalRandom;

/**
 * TTL of {@code base ± fraction}, drawn per entry. Entries written together (after a deploy, or a
 * generation bump) then expire spread out instead of all in the same second, which would send
 * every one of their readers to the database at once.
 */
public record JitteredTtl(Duration base, double fraction) implements RedisCacheWriter.TtlFunction {

    public JitteredTtl {
        if (fraction < 0 || fraction >= 1) {
            throw new IllegalArgumentException("fraction must be in [0, 1)");
        }
    }

    @Override
    public Duration getTimeToLive(Object key, Object value) {
        long baseMillis = base.toMillis();
        long spread = Math.round(baseMillis * fraction);
        return Duration.ofMillis(baseMillis + ThreadLocalRandom.current().nextLong(-spread, spread + 1));
    }
}
