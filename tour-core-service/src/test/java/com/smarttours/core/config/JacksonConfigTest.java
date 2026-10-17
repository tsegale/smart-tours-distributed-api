package com.smarttours.core.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.smarttours.core.model.Tour;
import org.junit.jupiter.api.Test;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;
import org.locationtech.jts.geom.PrecisionModel;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JacksonConfigTest {

    private final GeometryFactory geometryFactory = new GeometryFactory(new PrecisionModel(), 4326);

    @Test
    void jtsModule_serializesPointToGeoJson() throws Exception {
        ObjectMapper objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JacksonConfig().jtsModule());

        Point point = geometryFactory.createPoint(new Coordinate(2.2945, 48.8584));
        Tour tour = new Tour("Eiffel Tower", 49.99, point);
        tour.setId(1L);

        String json = objectMapper.writeValueAsString(tour);
        assertNotNull(json);
        assertTrue(json.contains("\"type\":\"Point\""));
        assertTrue(json.contains("2.2945"));
        assertTrue(json.contains("48.8584"));
    }
}
