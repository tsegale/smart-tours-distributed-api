package com.smarttours.core.tour;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Runs the real Flyway migrations against real PostGIS, then checks the radius query.
 * Skipped automatically when Docker isn't available; always runs in CI.
 */
@SpringBootTest
@Testcontainers(disabledWithoutDocker = true)
class TourRepositoryIntegrationTest {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer POSTGIS = new PostgreSQLContainer(
            DockerImageName.parse("postgis/postgis:16-3.4").asCompatibleSubstituteFor("postgres"));

    private static final GeometryFactory GEOMETRY = new GeometryFactory(new PrecisionModel(), 4326);

    // Windhoek city centre
    private static final double WINDHOEK_LAT = -22.5609;
    private static final double WINDHOEK_LON = 17.0658;

    @MockitoBean
    private RabbitTemplate rabbitTemplate; // no broker needed for repository tests

    @Autowired
    private TourRepository tours;

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
    void savedTour_roundTripsLocationAndExactPrice() {
        Tour saved = tours.findWithinRadius(WINDHOEK_LAT, WINDHOEK_LON, 6_000, 1).getFirst();

        assertThat(saved.getLatitude()).isEqualTo(-22.5236);
        assertThat(saved.getLongitude()).isEqualTo(17.0447);
        assertThat(saved.getPrice()).isEqualByComparingTo("450.00");
        assertThat(saved.getCreatedAt()).isNotNull();
    }

    private static Tour tour(String name, double lat, double lon) {
        return new Tour(name, null, new BigDecimal("450.00"), GEOMETRY.createPoint(new Coordinate(lon, lat)));
    }
}
