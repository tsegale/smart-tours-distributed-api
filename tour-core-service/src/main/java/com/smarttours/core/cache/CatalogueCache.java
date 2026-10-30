package com.smarttours.core.cache;

import com.smarttours.core.search.SearchCriteria;
import com.smarttours.core.search.SearchResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import org.springframework.cache.Cache;
import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.CachingConfigurer;
import org.springframework.cache.interceptor.CacheErrorHandler;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * Cache-aside for the catalogue (ADR 0009).
 *
 * <ul>
 *   <li><b>{@code tour:{id}}</b>: evicted when that tour is updated or rated.</li>
 *   <li><b>Search first pages</b>: keyed by {@code {generation}:{sha256(canonical search)}}. Any catalogue
 *       change bumps the generation (one Redis INCR), so every cached search is invalidated at once without
 *       SCAN/KEYS: old keys are never read again and expire on their own. The generation is read before the
 *       database, so a result computed from older data is filed under the older generation, where nobody
 *       looks for it.</li>
 * </ul>
 *
 * <p>Stampede protection is in-process single-flight: concurrent misses for one key in one instance share
 * a single load. Across N instances up to N loads of a key can still run at once; with a 60 s TTL and few
 * instances that is a handful of queries, so no distributed lock (ADR 0009).
 *
 * <p>Every Redis call is guarded: on any error the handler logs and counts it, and the caller falls
 * through to the database.
 */
@Component
public class CatalogueCache {

    public static final String TOURS = "tour";
    public static final String SEARCH = "search";
    static final String GENERATION_KEY = "search:generation";

    private final Cache tours;
    private final Cache searches;
    private final StringRedisTemplate redis;
    private final CacheErrorHandler errors;
    private final ConcurrentHashMap<String, CompletableFuture<Object>> inFlight = new ConcurrentHashMap<>();

    public CatalogueCache(CacheManager cacheManager, StringRedisTemplate redis, CachingConfigurer cachingConfigurer) {
        this.tours = Objects.requireNonNull(cacheManager.getCache(TOURS), TOURS);
        this.searches = Objects.requireNonNull(cacheManager.getCache(SEARCH), SEARCH);
        this.redis = redis;
        this.errors = Objects.requireNonNull(cachingConfigurer.errorHandler());
    }

    public VersionedTour tour(long id, Supplier<VersionedTour> loader) {
        return getOrLoad(tours, id, VersionedTour.class, loader);
    }

    /** Only first pages are cached: deep pages are rarer and their cursors make every key unique. */
    public SearchResponse firstPage(SearchCriteria criteria, Supplier<SearchResponse> loader) {
        String generation = generation();
        if (generation == null) {
            return loader.get(); // Redis unreachable: don't cache under a guessed generation
        }
        return getOrLoad(searches, generation + ":" + SearchCriteria.sha256(criteria.canonical()),
                SearchResponse.class, loader);
    }

    /** A tour changed: drop it, and every cached search with it. */
    public void tourChanged(long id) {
        try {
            tours.evict(id);
        } catch (RuntimeException ex) {
            errors.handleCacheEvictError(ex, tours, id);
        }
        catalogueChanged();
    }

    /** The catalogue changed (a new tour): every cached search may be out of date. */
    public void catalogueChanged() {
        try {
            redis.opsForValue().increment(GENERATION_KEY);
        } catch (RuntimeException ex) {
            errors.handleCacheEvictError(ex, searches, GENERATION_KEY);
        }
    }

    private String generation() {
        try {
            String value = redis.opsForValue().get(GENERATION_KEY);
            return value == null ? "0" : value;
        } catch (RuntimeException ex) {
            errors.handleCacheGetError(ex, searches, GENERATION_KEY);
            return null;
        }
    }

    private <T> T getOrLoad(Cache cache, Object key, Class<T> type, Supplier<T> loader) {
        T cached = get(cache, key, type);
        if (cached != null) {
            return cached;
        }
        String flightKey = cache.getName() + "::" + key;
        CompletableFuture<Object> mine = new CompletableFuture<>();
        CompletableFuture<Object> leader = inFlight.putIfAbsent(flightKey, mine);
        if (leader != null) {
            return type.cast(join(leader));
        }
        try {
            T loaded = loader.get();
            put(cache, key, loaded);
            mine.complete(loaded);
            return loaded;
        } catch (RuntimeException ex) {
            mine.completeExceptionally(ex);
            throw ex;
        } finally {
            inFlight.remove(flightKey, mine);
        }
    }

    private <T> T get(Cache cache, Object key, Class<T> type) {
        try {
            return cache.get(key, type);
        } catch (RuntimeException ex) {
            errors.handleCacheGetError(ex, cache, key);
            return null;
        }
    }

    private void put(Cache cache, Object key, Object value) {
        try {
            cache.put(key, value);
        } catch (RuntimeException ex) {
            errors.handleCachePutError(ex, cache, key, value);
        }
    }

    private static Object join(CompletableFuture<Object> future) {
        try {
            return future.join();
        } catch (CompletionException ex) {
            // The leader's load failed: followers fail the same way (a 404 stays a 404).
            if (ex.getCause() instanceof RuntimeException cause) {
                throw cause;
            }
            throw ex;
        }
    }
}
