package com.smarttours.core.search;

import com.smarttours.core.support.IntegrationTest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.StreamSupport;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * GET /api/tours/search against real PostGIS with the real migrations: each filter alone, realistic
 * combinations, every sort order, complete page walks over many ties, and the 400 cases.
 */
@AutoConfigureMockMvc
class TourSearchIntegrationTest extends IntegrationTest {

    private static final ZoneId WINDHOEK = ZoneId.of("Africa/Windhoek");
    private static final LocalDate DAY = LocalDate.now(WINDHOEK).plusDays(30);

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private JsonMapper json;

    private final Map<String, Long> ids = new HashMap<>();
    private final List<Long> tieIds = new ArrayList<>();

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE tours, tour_departure_summary RESTART IDENTITY CASCADE").update();
        // name, description, price, lat, lon, category, difficulty, minutes, rating sum, count, days ago
        tour("Windhoek City Walk", "Historic buildings and Christuskirche", "350.00", -22.5700, 17.0836,
                "CITY", "EASY", 180, 9, 2, 10);
        tour("Katutura Food Tour", "Taste kapana street food in Katutura", "450.00", -22.5236, 17.0447,
                "CULTURAL", "EASY", 240, 14, 3, 20);
        tour("Daan Viljoen Hike", "Mountain trail with zebra and kudu", "600.00", -22.5500, 16.9700,
                "ADVENTURE", "MODERATE", 300, 0, 0, 30);
        tour("Swakopmund Dune Quad", "Ride a quad bike over the desert sand", "1200.00", -22.6784, 14.5266,
                "ADVENTURE", "CHALLENGING", 120, 45, 10, 5);
        tour("Sossusvlei Sunrise Dunes", "Climb Dune 45 at dawn above the red desert", "1850.00", -24.7275, 15.3424,
                "DESERT", "MODERATE", 480, 48, 10, 1);
        tour("Etosha Game Drive", "Lions and elephants at the waterholes", "2400.00", -18.8556, 16.3294,
                "SAFARI", "EASY", 600, 23, 5, 40);
        tour("Cape Town Desert Photography", "Long drive to photograph the Karoo desert", "3100.00", -33.9249,
                18.4241, "PHOTOGRAPHY", "EXTREME", 2880, 4, 1, 2);
        // Forty tours identical in every sort key: same spot, price, rating and creation time.
        // Only the id tells them apart, so page walks must lean entirely on the tie-breaker.
        for (int i = 0; i < 40; i++) {
            tieIds.add(tour("Tie Tour " + i, "Identical desert walk", "500.00", -22.0, 17.0, "DESERT", "EASY", 120,
                    16, 4, 15));
        }
        departure(1, ids.get("Sossusvlei Sunrise Dunes"), DAY, 2);
        departure(2, ids.get("Etosha Game Drive"), DAY.plusDays(10), 6);
        departure(3, ids.get("Etosha Game Drive"), DAY.minusDays(60), 6);   // already departed
        departure(4, ids.get("Swakopmund Dune Quad"), DAY, 0);             // sold out
        departure(5, ids.get("Windhoek City Walk"), DAY.plusDays(2), 12);
    }

    // ---------------------------------------------------------------- filters alone

    @Test
    void radius_returnsOnlyToursInside_nearestFirst_withDistances() throws Exception {
        JsonNode page = search(params("lat", "-22.5609", "lon", "17.0658", "radius", "25000"));

        assertThat(names(page)).containsExactly("Windhoek City Walk", "Katutura Food Tour", "Daan Viljoen Hike");
        List<Double> distances = values(page, "distanceMeters").stream().map(JsonNode::asDouble).toList();
        assertThat(distances).isSorted().allSatisfy(d -> assertThat(d).isBetween(0.0, 25_000.0));
        assertThat(distances.getFirst()).isBetween(2_000.0, 3_000.0); // city centre to Christuskirche
    }

    @Test
    void bbox_returnsToursInsideTheBox() throws Exception {
        JsonNode page = search(params("bbox", "14.0,-25.0,16.0,-22.0", "size", "100"));

        assertThat(names(page)).containsExactlyInAnyOrder("Swakopmund Dune Quad", "Sossusvlei Sunrise Dunes");
        assertThat(page.get("items").get(0).get("distanceMeters").isNull()).isTrue();
    }

    @Test
    void fullText_matchesStemsAndRanksNameAboveDescription() throws Exception {
        JsonNode page = search(params("q", "dunes", "size", "100"));

        // "dunes" and "dune" share the stem "dune". Sossusvlei matches in name (weight A) and description (B),
        // Swakopmund in its name only, so Sossusvlei ranks higher.
        assertThat(names(page)).containsExactly("Sossusvlei Sunrise Dunes", "Swakopmund Dune Quad");
    }

    @Test
    void fullText_supportsWebSearchSyntax() throws Exception {
        JsonNode page = search(params("q", "desert -photograph -identical", "size", "100"));

        assertThat(names(page)).containsExactlyInAnyOrder("Swakopmund Dune Quad", "Sossusvlei Sunrise Dunes");
    }

    @Test
    void price_rangeIsInclusive() throws Exception {
        assertThat(names(search(params("minPrice", "1850", "size", "100"))))
                .containsExactlyInAnyOrder("Sossusvlei Sunrise Dunes", "Etosha Game Drive",
                        "Cape Town Desert Photography");
        assertThat(names(search(params("maxPrice", "450.00", "size", "100"))))
                .containsExactlyInAnyOrder("Windhoek City Walk", "Katutura Food Tour");
    }

    @Test
    void category_isMultiValued_repeatedOrCommaSeparated_caseInsensitive() throws Exception {
        List<String> repeated = names(search(params("category", "safari", "category", "CITY")));
        List<String> commas = names(search(params("category", "SAFARI,city")));

        assertThat(repeated).containsExactlyInAnyOrder("Etosha Game Drive", "Windhoek City Walk");
        assertThat(commas).isEqualTo(repeated);
    }

    @Test
    void difficulty_filters() throws Exception {
        assertThat(names(search(params("difficulty", "CHALLENGING,EXTREME"))))
                .containsExactlyInAnyOrder("Swakopmund Dune Quad", "Cape Town Desert Photography");
    }

    @Test
    void duration_rangeIsInclusive() throws Exception {
        assertThat(names(search(params("minDuration", "480", "maxDuration", "600"))))
                .containsExactlyInAnyOrder("Sossusvlei Sunrise Dunes", "Etosha Game Drive");
    }

    @Test
    void minRating_excludesUnratedAndLowerRated() throws Exception {
        assertThat(names(search(params("minRating", "4.5"))))
                .containsExactlyInAnyOrder("Windhoek City Walk", "Katutura Food Tour", "Swakopmund Dune Quad",
                        "Sossusvlei Sunrise Dunes", "Etosha Game Drive");
    }

    @Test
    void departures_needAFutureDepartureInTheWindowWithEnoughSeats() throws Exception {
        assertThat(names(search(params("from", DAY.toString(), "to", DAY.toString()))))
                .as("Swakopmund's departure that day is sold out")
                .containsExactly("Sossusvlei Sunrise Dunes");
        assertThat(names(search(params("from", DAY.toString(), "to", DAY.plusDays(10).toString(), "minSlots", "5"))))
                .containsExactlyInAnyOrder("Etosha Game Drive", "Windhoek City Walk");
        assertThat(names(search(params("minSlots", "10"))))
                .as("no dates: any future departure; Etosha's 6 seats are not enough")
                .containsExactly("Windhoek City Walk");
    }

    // ---------------------------------------------------------------- combinations

    @Test
    void radiusPlusPriceSortedByDistance() throws Exception {
        JsonNode page = search(params("lat", "-22.5609", "lon", "17.0658", "radius", "400000", "maxPrice", "1500",
                "sort", "distance", "size", "100"));

        List<String> names = names(page);
        assertThat(names).startsWith("Windhoek City Walk", "Katutura Food Tour", "Daan Viljoen Hike")
                .endsWith("Swakopmund Dune Quad")
                .doesNotContain("Sossusvlei Sunrise Dunes");
        assertThat(names).hasSize(3 + tieIds.size() + 1);
    }

    @Test
    void fullTextPlusCategoryPlusRating() throws Exception {
        assertThat(names(search(params("q", "desert", "category", "DESERT,ADVENTURE", "minRating", "4.5"))))
                .containsExactlyInAnyOrder("Sossusvlei Sunrise Dunes", "Swakopmund Dune Quad");
    }

    @Test
    void bboxPlusDateAvailability() throws Exception {
        JsonNode page = search(params("bbox", "13.0,-26.0,18.0,-18.0", "from", DAY.toString(),
                "to", DAY.plusDays(14).toString(), "sort", "price_asc"));

        assertThat(names(page)).containsExactly("Windhoek City Walk", "Sossusvlei Sunrise Dunes", "Etosha Game Drive");
    }

    @Test
    void noMatch_returnsAnEmptyPageWithoutCursor() throws Exception {
        mockMvc.perform(request(params("q", "glacier")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty())
                .andExpect(jsonPath("$.size").value(0))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
    }

    // ---------------------------------------------------------------- sort orders and paging

    @Test
    void sortOrders() throws Exception {
        assertThat(prices(search(params("sort", "price_asc", "size", "100")))).isSorted();
        assertThat(prices(search(params("sort", "price_desc", "size", "100"))))
                .isSortedAccordingTo(Comparator.reverseOrder());
        List<String> byRating = names(search(params("sort", "rating", "size", "3")));
        assertThat(byRating).containsExactly("Sossusvlei Sunrise Dunes", "Katutura Food Tour", "Etosha Game Drive");
        assertThat(names(search(params("sort", "newest", "size", "2"))))
                .containsExactly("Sossusvlei Sunrise Dunes", "Cape Town Desert Photography");
    }

    @Test
    void defaultSort_isRelevanceWithQ_distanceWithAPoint_elseNewest() throws Exception {
        assertThat(names(search(params("q", "dunes"))).getFirst()).isEqualTo("Sossusvlei Sunrise Dunes");
        assertThat(names(search(params("lat", "-22.5700", "lon", "17.0836"))).getFirst())
                .isEqualTo("Windhoek City Walk");
        assertThat(names(search(params())).getFirst()).isEqualTo("Sossusvlei Sunrise Dunes");
    }

    @ParameterizedTest
    @EnumSource(SearchSort.class)
    void walkingAllPages_returnsEveryMatchExactlyOnce_evenWithManyTies(SearchSort sort) throws Exception {
        MultiValueMap<String, String> filters = params("sort", sort.param(), "size", "7");
        if (sort == SearchSort.RELEVANCE) {
            filters.add("q", "desert");
        }
        if (sort == SearchSort.DISTANCE) {
            filters.addAll(params("lat", "-22.0", "lon", "17.0"));
        }
        List<Long> expected = allIds(filters);

        List<Long> seen = new ArrayList<>();
        String cursor = null;
        int pages = 0;
        do {
            MultiValueMap<String, String> pageParams = new LinkedMultiValueMap<>(filters);
            if (cursor != null) {
                pageParams.add("cursor", cursor);
            }
            JsonNode page = search(pageParams);
            values(page, "id").forEach(id -> seen.add(id.asLong()));
            cursor = page.get("nextCursor").isNull() ? null : page.get("nextCursor").asString();
            pages++;
        } while (cursor != null && pages < 50);

        assertThat(seen).as("no row twice").doesNotHaveDuplicates();
        assertThat(seen).as("every row once, in the same order as one big page").isEqualTo(expected);
        assertThat(seen).containsAll(tieIds);
    }

    @Test
    void cursor_isRefusedForADifferentSearch() throws Exception {
        String cursor = search(params("sort", "price_asc", "size", "2")).get("nextCursor").asString();

        mockMvc.perform(request(params("sort", "price_asc", "size", "2", "maxPrice", "900", "cursor", cursor)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].param").value("cursor"));
        mockMvc.perform(request(params("sort", "price_desc", "size", "2", "cursor", cursor)))
                .andExpect(status().isBadRequest());
        // Page size may change between pages.
        mockMvc.perform(request(params("sort", "price_asc", "size", "5", "cursor", cursor)))
                .andExpect(status().isOk());
    }

    // ---------------------------------------------------------------- validation

    @Test
    void contradictoryOrOutOfRangeParameters_are400ProblemDetails() throws Exception {
        assertInvalid(params("lat", "-22.5", "lon", "17.0", "radius", "1000", "bbox", "14,-25,16,-22"), "bbox");
        assertInvalid(params("radius", "1000"), "radius");
        assertInvalid(params("lat", "-22.5"), "lat");
        assertInvalid(params("lat", "-95", "lon", "17"), "lat");
        assertInvalid(params("lat", "-22.5", "lon", "17", "radius", "600000"), "radius");
        assertInvalid(params("bbox", "170,-20,-170,-10"), "bbox"); // crosses the antimeridian
        assertInvalid(params("bbox", "14,-22,16"), "bbox");
        assertInvalid(params("bbox", "14,-20,16,-25"), "bbox");
        assertInvalid(params("minPrice", "900", "maxPrice", "100"), "minPrice");
        assertInvalid(params("minDuration", "600", "maxDuration", "60"), "minDuration");
        assertInvalid(params("minRating", "5.5"), "minRating");
        assertInvalid(params("from", "2030-02-01", "to", "2030-01-01"), "from");
        assertInvalid(params("minSlots", "0"), "minSlots");
        assertInvalid(params("size", "101"), "size");
        assertInvalid(params("sort", "relevance"), "sort");
        assertInvalid(params("sort", "distance"), "sort");
        assertInvalid(params("cursor", "not-a-cursor"), "cursor");
        assertInvalid(params("q", "x".repeat(201)), "q");
    }

    @Test
    void unparseableValues_are400ProblemDetails() throws Exception {
        for (MultiValueMap<String, String> bad : List.of(params("lat", "abc", "lon", "17"),
                params("category", "SPACE"), params("sort", "cheapest"), params("from", "next week"))) {
            mockMvc.perform(request(bad))
                    .andExpect(status().isBadRequest())
                    .andExpect(content().contentType(MediaType.APPLICATION_PROBLEM_JSON));
        }
    }

    @Test
    void manyProblemsAreReportedTogether() throws Exception {
        mockMvc.perform(request(params("minPrice", "900", "maxPrice", "100", "size", "0")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors.length()").value(2));
    }

    // ---------------------------------------------------------------- helpers

    private void assertInvalid(MultiValueMap<String, String> params, String param) throws Exception {
        mockMvc.perform(request(params))
                .andExpect(status().isBadRequest())
                .andExpect(content().contentType(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.type").value("https://smarttours.dev/problems/invalid-search"))
                .andExpect(jsonPath("$.errors[?(@.param == '" + param + "')]").exists());
    }

    private List<Long> allIds(MultiValueMap<String, String> filters) throws Exception {
        MultiValueMap<String, String> all = new LinkedMultiValueMap<>(filters);
        all.set("size", "100");
        JsonNode page = search(all);
        assertThat(page.get("nextCursor").isNull()).as("fits one page of 100").isTrue();
        return values(page, "id").stream().map(JsonNode::asLong).toList();
    }

    private JsonNode search(MultiValueMap<String, String> params) throws Exception {
        String body = mockMvc.perform(request(params))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return json.readTree(body);
    }

    private static MockHttpServletRequestBuilder request(MultiValueMap<String, String> params) {
        return get("/api/tours/search").queryParams(params);
    }

    private static MultiValueMap<String, String> params(String... pairs) {
        MultiValueMap<String, String> params = new LinkedMultiValueMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            params.add(pairs[i], pairs[i + 1]);
        }
        return params;
    }

    private static List<JsonNode> values(JsonNode page, String field) {
        return StreamSupport.stream(page.get("items").spliterator(), false).map(item -> item.get(field)).toList();
    }

    private static List<String> names(JsonNode page) {
        return values(page, "name").stream().map(JsonNode::asString).toList();
    }

    private static List<BigDecimal> prices(JsonNode page) {
        return values(page, "price").stream().map(JsonNode::decimalValue).toList();
    }

    private long tour(String name, String description, String price, double lat, double lon, String category,
                      String difficulty, int minutes, int ratingSum, int ratingCount, int daysAgo) {
        long id = jdbc.sql("""
                        INSERT INTO tours (name, description, price, currency, location, category, difficulty,
                                           duration_minutes, rating_sum, rating_count, created_at, updated_at)
                        VALUES (:name, :description, :price, 'NAD', ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                                :category, :difficulty, :minutes, :ratingSum, :ratingCount,
                                timestamptz '2026-10-01 12:00:00+00' - make_interval(days => :daysAgo),
                                timestamptz '2026-10-01 12:00:00+00')
                        RETURNING id""")
                .param("name", name).param("description", description).param("price", new BigDecimal(price))
                .param("lat", lat).param("lon", lon).param("category", category).param("difficulty", difficulty)
                .param("minutes", minutes).param("ratingSum", ratingSum).param("ratingCount", ratingCount)
                .param("daysAgo", daysAgo)
                .query(Long.class).single();
        ids.put(name, id);
        return id;
    }

    private void departure(long availabilityId, long tourId, LocalDate day, int slotsLeft) {
        jdbc.sql("""
                        INSERT INTO tour_departure_summary (availability_id, tour_id, starts_at, total_slots,
                                                            slots_left, source_version)
                        VALUES (?, ?, ?, 12, ?, 1)""")
                .params(availabilityId, tourId, day.atTime(6, 0).atZone(WINDHOEK).toOffsetDateTime(), slotsLeft)
                .update();
    }
}
