package com.smarttours.core.tour;

import com.smarttours.core.support.IntegrationTest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Runs the real Flyway migrations against real PostGIS, then checks the radius query and the V2 columns. */
class TourRepositoryIntegrationTest extends IntegrationTest {

    private static final GeometryFactory GEOMETRY = new GeometryFactory(new PrecisionModel(), 4326);

    // Windhoek city centre
    private static final double WINDHOEK_LAT = -22.5609;
    private static final double WINDHOEK_LON = 17.0658;

    @Autowired
    private TourRepository tours;

    @Autowired
    private JdbcClient jdbc;

    @BeforeEach
    void seed() {
        tours.deleteAll();
        tours.saveAll(List.of(
                tour("Katutura Township Walk", -22.5236, 17.0447),  // ~5 km from the centre
                tour("Daan Viljoen Hike", -22.5500, 16.9700),       // ~10 km
                tour("Swakopmund Dune Quad", -22.6784, 14.5266)));  // ~260 km
    }

    @Test
    void findWithinRadius_returnsOnlyToursInsideRadius_nearestFirst() {
        List<Tour> result = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 25_000, 100);

        assertThat(result).extracting(Tour::getName)
                .containsExactly("Katutura Township Walk", "Daan Viljoen Hike");
    }

    @Test
    void findWithinRadius_largeRadiusIncludesCoast() {
        List<Tour> result = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 300_000, 100);

        assertThat(result).hasSize(3);
        assertThat(result.getLast().getName()).isEqualTo("Swakopmund Dune Quad");
    }

    @Test
    void savedTour_roundTripsLocationExactPriceAndFacets() {
        Tour saved = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 6_000, 1).getFirst();

        assertThat(saved.getLatitude()).isEqualTo(-22.5236);
        assertThat(saved.getLongitude()).isEqualTo(17.0447);
        assertThat(saved.getPrice()).isEqualByComparingTo("450.00");
        assertThat(saved.getCategory()).isEqualTo(Category.CULTURAL);
        assertThat(saved.getDifficulty()).isEqualTo(Difficulty.EASY);
        assertThat(saved.getRatingCount()).isZero();
        assertThat(saved.getAvgRating()).isEqualByComparingTo("0.00");
        assertThat(saved.getCreatedAt()).isNotNull();
    }

    @Test
    void searchVector_isGeneratedWithNameWeighedAboveDescription() {
        Tour saved = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 6_000, 1).getFirst();

        String vector = jdbc.sql("SELECT search_vector::text FROM tours WHERE id = ?")
                .param(saved.getId()).query(String.class).single();

        assertThat(vector).contains("'katutura':1A", "'township':2A");
    }

    @Test
    void avgRating_isDerivedFromSumAndCount() {
        long id = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 6_000, 1).getFirst().getId();

        jdbc.sql("UPDATE tours SET rating_sum = 14, rating_count = 3 WHERE id = ?").param(id).update();

        assertThat(jdbc.sql("SELECT avg_rating FROM tours WHERE id = ?").param(id).query(BigDecimal.class).single())
                .isEqualByComparingTo("4.67");
    }

    @Test
    void checkConstraints_rejectImpossibleRatingsAndUnknownCategories() {
        long id = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 6_000, 1).getFirst().getId();

        assertThatThrownBy(() -> jdbc.sql("UPDATE tours SET rating_sum = 6, rating_count = 1 WHERE id = ?")
                .param(id).update()).hasMessageContaining("ck_tours_rating_range");
        assertThatThrownBy(() -> jdbc.sql("UPDATE tours SET category = 'SPACE' WHERE id = ?")
                .param(id).update()).hasMessageContaining("tours_category_check");
    }

    private static Tour tour(String name, double lat, double lon) {
        return new Tour(name, "A walk", new BigDecimal("450.00"), GEOMETRY.createPoint(new Coordinate(lon, lat)),
                Category.CULTURAL, Difficulty.EASY, 120);
    }
}
