package com.smarttours.core.cache;

import com.smarttours.core.search.SearchResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import io.lettuce.core.ClientOptions;
import io.lettuce.core.TimeoutOptions;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.cache.autoconfigure.RedisCacheManagerBuilderCustomizer;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
import org.springframework.cache.annotation.CachingConfigurer;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cache.interceptor.CacheErrorHandler;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.serializer.JacksonJsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializationContext.SerializationPair;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;

/**
 * Spring Cache on Redis (ADR 0009). Two caches, each with a typed JSON serializer (Jackson 3, no
 * polymorphic type information in the payload) and a jittered TTL:
 * <ul>
 *   <li>{@value CatalogueCache#TOURS}: one tour by id, 10 minutes, evicted on update and on a new rating;</li>
 *   <li>{@value CatalogueCache#SEARCH}: first pages of searches, 60 seconds, keyed by catalogue generation.</li>
 * </ul>
 * Redis is an optimisation, never a dependency: every cache error goes to {@link FallThroughCacheErrorHandler},
 * and Lettuce rejects commands at once while disconnected instead of queueing them until the timeout.
 */
@Configuration
@EnableCaching
public class CacheConfig implements CachingConfigurer {

    static final Duration TOUR_TTL = Duration.ofMinutes(10);
    static final Duration SEARCH_TTL = Duration.ofSeconds(60);
    static final double TTL_JITTER = 0.1;

    private final MeterRegistry meterRegistry;

    public CacheConfig(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    @Override
    public CacheErrorHandler errorHandler() {
        return new FallThroughCacheErrorHandler(meterRegistry);
    }

    @Bean
    RedisCacheManagerBuilderCustomizer catalogueCaches(JsonMapper jsonMapper) {
        return builder -> builder
                .withCacheConfiguration(CatalogueCache.TOURS, cacheConfig(jsonMapper, VersionedTour.class, TOUR_TTL))
                .withCacheConfiguration(CatalogueCache.SEARCH,
                        cacheConfig(jsonMapper, SearchResponse.class, SEARCH_TTL));
    }

    /**
     * Fail fast while Redis is unreachable: by default Lettuce queues commands during reconnects, so
     * every request would wait the full command timeout before falling through to the database.
     */
    @Bean
    LettuceClientConfigurationBuilderCustomizer failFastWhenRedisIsDown() {
        return builder -> builder.clientOptions(ClientOptions.builder()
                .disconnectedBehavior(ClientOptions.DisconnectedBehavior.REJECT_COMMANDS)
                .autoReconnect(true)
                .timeoutOptions(TimeoutOptions.enabled())
                .build());
    }

    private static <T> RedisCacheConfiguration cacheConfig(JsonMapper jsonMapper, Class<T> type, Duration ttl) {
        return RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(new JitteredTtl(ttl, TTL_JITTER))
                .disableCachingNullValues()
                .serializeValuesWith(SerializationPair.fromSerializer(new JacksonJsonRedisSerializer<>(jsonMapper, type)));
    }
}
