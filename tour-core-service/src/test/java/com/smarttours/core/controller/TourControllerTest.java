package com.smarttours.core.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.smarttours.core.config.JacksonConfig;
import com.smarttours.core.model.Tour;
import com.smarttours.core.repository.TourRepository;
import org.junit.jupiter.api.Test;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Collections;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(TourController.class)
@Import(JacksonConfig.class)
class TourControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private TourRepository tourRepository;

    private final GeometryFactory geometryFactory = new GeometryFactory(new PrecisionModel(), 4326);

    @Test
    void createTour_returnsSavedTour() throws Exception {
        Point point = geometryFactory.createPoint(new Coordinate(2.2945, 48.8584));
        Tour savedTour = new Tour("Eiffel Tower Visit", 49.99, point);
        savedTour.setId(1L);

        when(tourRepository.save(any(Tour.class))).thenReturn(savedTour);

        String jsonPayload = """
                {
                    "name": "Eiffel Tower Visit",
                    "price": 49.99,
                    "latitude": 48.8584,
                    "longitude": 2.2945
                }
                """;

        mockMvc.perform(post("/api/tours")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(jsonPayload))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.name").value("Eiffel Tower Visit"))
                .andExpect(jsonPath("$.price").value(49.99));
    }

    @Test
    void getAllTours_returnsTourList() throws Exception {
        Point point = geometryFactory.createPoint(new Coordinate(2.2945, 48.8584));
        Tour tour = new Tour("Eiffel Tower Visit", 49.99, point);
        tour.setId(1L);

        when(tourRepository.findAll()).thenReturn(Collections.singletonList(tour));

        mockMvc.perform(get("/api/tours"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(1))
                .andExpect(jsonPath("$[0].name").value("Eiffel Tower Visit"));
    }

    @Test
    void getTourById_found_returnsTour() throws Exception {
        Point point = geometryFactory.createPoint(new Coordinate(2.2945, 48.8584));
        Tour tour = new Tour("Eiffel Tower Visit", 49.99, point);
        tour.setId(1L);

        when(tourRepository.findById(1L)).thenReturn(Optional.of(tour));

        mockMvc.perform(get("/api/tours/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.name").value("Eiffel Tower Visit"));
    }

    @Test
    void getTourById_notFound_returns404() throws Exception {
        when(tourRepository.findById(99L)).thenReturn(Optional.empty());

        mockMvc.perform(get("/api/tours/99"))
                .andExpect(status().isNotFound());
    }

    @Test
    void getNearbyTours_returnsNearbyList() throws Exception {
        Point point = geometryFactory.createPoint(new Coordinate(2.2945, 48.8584));
        Tour tour = new Tour("Eiffel Tower Visit", 49.99, point);
        tour.setId(1L);

        when(tourRepository.findToursWithinRadius(eq(2.2945), eq(48.8584), eq(5000.0)))
                .thenReturn(Collections.singletonList(tour));

        mockMvc.perform(get("/api/tours/nearby")
                        .param("lon", "2.2945")
                        .param("lat", "48.8584")
                        .param("radius", "5000.0"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(1))
                .andExpect(jsonPath("$[0].name").value("Eiffel Tower Visit"));
    }
}
