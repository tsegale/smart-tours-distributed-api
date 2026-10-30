package com.smarttours.core.cache;

import com.smarttours.core.support.SharedContainers;
import com.smarttours.core.support.TourFixtures;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.context.ImportTestcontainers;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Redis is stopped in the middle of the test: search, reads and writes must keep working, from the
 * database, and the failures must show up in {@code cache.errors}. Uses a Redis of its own, so
 * stopping it can't affect the other tests (hence not extending {@code IntegrationTest}).
 */
@SpringBootTest
@AutoConfigureMockMvc
@ImportTestcontainers(SharedContainers.class)
@Testcontainers(disabledWithoutDocker = true)
class RedisOutageIntegrationTest {

    private static final GenericContainer<?> DOOMED_REDIS = SharedContainers.redis();

    static {
        DOOMED_REDIS.start();
    }

    @DynamicPropertySource
    static void doomedRedis(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", DOOMED_REDIS::getHost);
        registry.add("spring.data.redis.port", () -> DOOMED_REDIS.getMappedPort(6379));
    }

    @AfterAll
    static void cleanUp() {
        DOOMED_REDIS.stop();
    }

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private MeterRegistry meters;

    @Test
    void searchReadsAndWritesSurviveRedisGoingAway() throws Exception {
        String location = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("Sossusvlei Sunrise Dunes", "Outage Dunes")))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getHeader(HttpHeaders.LOCATION);
        mockMvc.perform(get("/api/tours/search").param("q", "outage")).andExpect(jsonPath("$.size").value(1));
        mockMvc.perform(get(location)).andExpect(status().isOk());

        DOOMED_REDIS.stop();

        long started = System.nanoTime();
        mockMvc.perform(get("/api/tours/search").param("q", "outage"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].name").value("Outage Dunes"));
        String etag = mockMvc.perform(get(location))
                .andExpect(status().isOk())
                .andReturn().getResponse().getHeader(HttpHeaders.ETAG);
        mockMvc.perform(put(location).header(HttpHeaders.IF_MATCH, etag).contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("Sossusvlei Sunrise Dunes", "Outage Dunes II")))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/tours/search").param("q", "outage"))
                .andExpect(jsonPath("$.items[0].name").value("Outage Dunes II"));
        long elapsedMs = (System.nanoTime() - started) / 1_000_000;

        double errors = meters.find("cache.errors").counters().stream().mapToDouble(c -> c.count()).sum();
        assertThat(errors).as("failures are counted, not hidden").isPositive();
        assertThat(elapsedMs).as("four requests, each bounded by the 250 ms command timeout at worst")
                .isLessThan(10_000);
    }
}
