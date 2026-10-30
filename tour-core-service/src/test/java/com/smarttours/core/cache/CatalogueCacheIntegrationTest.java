package com.smarttours.core.cache;

import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.support.TourFixtures;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The catalogue cache against real Redis. Rows are changed behind the cache's back with plain SQL
 * to tell a cached answer from a fresh one.
 */
@AutoConfigureMockMvc
class CatalogueCacheIntegrationTest extends IntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private StringRedisTemplate redis;

    private long tourId;

    @BeforeEach
    void setUp() throws Exception {
        jdbc.sql("TRUNCATE tours, tour_departure_summary RESTART IDENTITY CASCADE").update();
        tourId = createTour("Sossusvlei Sunrise Dunes");
    }

    @Test
    void tour_isServedFromTheCacheWithAJitteredTenMinuteTtl() throws Exception {
        mockMvc.perform(get("/api/tours/{id}", tourId)).andExpect(status().isOk());
        renameBehindTheCache(tourId, "Changed in the database only");

        mockMvc.perform(get("/api/tours/{id}", tourId))
                .andExpect(jsonPath("$.name").value("Sossusvlei Sunrise Dunes"));
        Long ttl = redis.getExpire("tour::" + tourId);
        assertThat(ttl).isBetween(540L, 660L);
    }

    @Test
    void update_evictsTheTourAndInvalidatesSearches() throws Exception {
        String etag = mockMvc.perform(get("/api/tours/{id}", tourId)).andReturn().getResponse()
                .getHeader(HttpHeaders.ETAG);
        mockMvc.perform(get("/api/tours/search").param("q", "sunrise")).andExpect(jsonPath("$.size").value(1));

        mockMvc.perform(put("/api/tours/{id}", tourId).header(HttpHeaders.IF_MATCH, etag)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("Sossusvlei Sunrise Dunes", "Sossusvlei at Dawn")))
                .andExpect(status().isOk());

        mockMvc.perform(get("/api/tours/{id}", tourId)).andExpect(jsonPath("$.name").value("Sossusvlei at Dawn"));
        mockMvc.perform(get("/api/tours/search").param("q", "sunrise")).andExpect(jsonPath("$.size").value(0));
    }

    @Test
    void searchFirstPage_isCachedUnderTheCurrentGeneration() throws Exception {
        mockMvc.perform(get("/api/tours/search").param("q", "sunrise")).andExpect(jsonPath("$.size").value(1));
        renameBehindTheCache(tourId, "Renamed without telling the cache");

        mockMvc.perform(get("/api/tours/search").param("q", "sunrise"))
                .andExpect(jsonPath("$.items[0].name").value("Sossusvlei Sunrise Dunes"));
        Set<String> keys = redis.keys("search::*");
        assertThat(keys).hasSize(1);
        assertThat(keys.iterator().next()).matches("search::1:[0-9a-f]{64}"); // setUp created a tour
        assertThat(redis.getExpire(keys.iterator().next())).isBetween(54L, 66L);
    }

    @Test
    void equivalentSearches_shareOneEntry() throws Exception {
        mockMvc.perform(get("/api/tours/search").param("category", "DESERT,SAFARI").param("maxPrice", "5000"));
        mockMvc.perform(get("/api/tours/search").param("maxPrice", "5000.00")
                .param("category", "safari").param("category", "desert"));

        assertThat(redis.keys("search::*")).hasSize(1);
    }

    @Test
    void newTour_bumpsTheGeneration_soCachedSearchesMissIt() throws Exception {
        mockMvc.perform(get("/api/tours/search").param("q", "dunes")).andExpect(jsonPath("$.size").value(1));

        createTour("Swakopmund Dunes by Quad");

        assertThat(redis.opsForValue().get(CatalogueCache.GENERATION_KEY)).isEqualTo("2"); // setUp's tour + this one
        mockMvc.perform(get("/api/tours/search").param("q", "dunes")).andExpect(jsonPath("$.size").value(2));
    }

    @Test
    void laterPages_areNeverCached() throws Exception {
        createTour("Second Dunes Tour");
        String cursor = mockMvc.perform(get("/api/tours/search").param("sort", "price_asc").param("size", "1"))
                .andReturn().getResponse().getContentAsString().replaceAll(".*\"nextCursor\":\"([^\"]+)\".*", "$1");
        int before = redis.keys("search::*").size();

        mockMvc.perform(get("/api/tours/search").param("sort", "price_asc").param("size", "1").param("cursor", cursor))
                .andExpect(status().isOk());

        assertThat(redis.keys("search::*")).hasSize(before);
    }

    private long createTour(String name) throws Exception {
        String location = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("Sossusvlei Sunrise Dunes", name)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getHeader(HttpHeaders.LOCATION);
        return Long.parseLong(location.substring(location.lastIndexOf('/') + 1));
    }

    private void renameBehindTheCache(long id, String name) {
        jdbc.sql("UPDATE tours SET name = ? WHERE id = ?").params(name, id).update();
    }
}
