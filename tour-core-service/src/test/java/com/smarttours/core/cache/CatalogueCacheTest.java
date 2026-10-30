package com.smarttours.core.cache;

import com.smarttours.core.tour.TourNotFoundException;
import com.smarttours.core.tour.dto.VersionedTour;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.cache.concurrent.ConcurrentMapCacheManager;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

/** Single-flight and jitter, without Redis. */
class CatalogueCacheTest {

    private final SimpleMeterRegistry meters = new SimpleMeterRegistry();
    private final CatalogueCache cache = new CatalogueCache(new ConcurrentMapCacheManager("tour", "search"),
            mock(StringRedisTemplate.class), new CacheConfig(meters));

    @Test
    void concurrentMissesForOneKey_loadOnce() throws Exception {
        AtomicInteger loads = new AtomicInteger();
        CountDownLatch release = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(16);
        try {
            List<Future<VersionedTour>> results = new ArrayList<>();
            for (int i = 0; i < 16; i++) {
                results.add(pool.submit(() -> cache.tour(7L, () -> {
                    loads.incrementAndGet();
                    await(release);
                    return new VersionedTour(null, 3);
                })));
            }
            Thread.sleep(200); // let every thread reach the cache and find the miss
            release.countDown();
            for (Future<VersionedTour> result : results) {
                assertThat(result.get(5, TimeUnit.SECONDS).version()).isEqualTo(3);
            }
        } finally {
            pool.shutdownNow();
        }
        assertThat(loads).hasValue(1);
    }

    @Test
    void failedLoad_isNotCached_andTheNextCallTriesAgain() {
        assertThatThrownBy(() -> cache.tour(9L, () -> {
            throw new TourNotFoundException(9L);
        })).isInstanceOf(TourNotFoundException.class);

        assertThat(cache.tour(9L, () -> new VersionedTour(null, 1)).version()).isEqualTo(1);
    }

    @Test
    void jitteredTtl_staysWithinTheBand_andVaries() {
        JitteredTtl ttl = new JitteredTtl(Duration.ofSeconds(60), 0.1);
        List<Duration> samples = new ArrayList<>();
        for (int i = 0; i < 200; i++) {
            samples.add(ttl.getTimeToLive("k", "v"));
        }
        assertThat(samples).allSatisfy(d -> assertThat(d).isBetween(Duration.ofSeconds(54), Duration.ofSeconds(66)));
        assertThat(samples.stream().distinct().count()).isGreaterThan(50);
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await(5, TimeUnit.SECONDS);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(ex);
        }
    }
}
