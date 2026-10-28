package com.smarttours.core.tour;

import com.smarttours.core.tour.dto.CreateTourRequest;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourResponse;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(TourController.class)
class TourControllerTest {

    private static final TourResponse SOSSUSVLEI = new TourResponse(
            1L, "Sossusvlei Sunrise Dunes", "Climb Dune 45 at dawn", new BigDecimal("1850.00"), "NAD",
            -24.7275, 15.3424, Instant.parse("2026-10-01T08:00:00Z"));

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private TourService tourService;

    @Test
    void create_returns201WithLocationHeader() throws Exception {
        when(tourService.create(any(CreateTourRequest.class))).thenReturn(SOSSUSVLEI);

        mockMvc.perform(post("/api/tours")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "Sossusvlei Sunrise Dunes", "description": "Climb Dune 45 at dawn",
                                 "price": 1850.00, "latitude": -24.7275, "longitude": 15.3424}
                                """))
                .andExpect(status().isCreated())
                .andExpect(header().string("Location", "http://localhost/api/tours/1"))
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.latitude").value(-24.7275))
                .andExpect(jsonPath("$.longitude").value(15.3424))
                .andExpect(jsonPath("$.currency").value("NAD"));
    }

    @Test
    void create_rejectsInvalidPayloadWithProblemDetails() throws Exception {
        mockMvc.perform(post("/api/tours")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "", "price": -5, "latitude": 123.0, "longitude": 15.0}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(header().string("Content-Type", MediaType.APPLICATION_PROBLEM_JSON_VALUE));

        verifyNoInteractions(tourService);
    }

    @Test
    void list_returnsStablePageShape() throws Exception {
        when(tourService.list(0, 20)).thenReturn(new PageResponse<>(List.of(SOSSUSVLEI), 0, 20, 1, 1));

        mockMvc.perform(get("/api/tours"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[0].name").value("Sossusvlei Sunrise Dunes"))
                .andExpect(jsonPath("$.totalElements").value(1));
    }

    @Test
    void list_rejectsOversizedPage() throws Exception {
        mockMvc.perform(get("/api/tours").param("size", "1000"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void get_found_returnsTour() throws Exception {
        when(tourService.get(1L)).thenReturn(SOSSUSVLEI);

        mockMvc.perform(get("/api/tours/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("Sossusvlei Sunrise Dunes"));
    }

    @Test
    void get_missing_returns404ProblemDetails() throws Exception {
        when(tourService.get(99L)).thenThrow(new TourNotFoundException(99L));

        mockMvc.perform(get("/api/tours/99"))
                .andExpect(status().isNotFound())
                .andExpect(header().string("Content-Type", MediaType.APPLICATION_PROBLEM_JSON_VALUE))
                .andExpect(jsonPath("$.title").value("Tour not found"))
                .andExpect(jsonPath("$.tourId").value(99));
    }

    @Test
    void nearby_returnsResults() throws Exception {
        when(tourService.nearby(-22.5609, 17.0658, 50_000)).thenReturn(List.of(SOSSUSVLEI));

        mockMvc.perform(get("/api/tours/nearby")
                        .param("lat", "-22.5609")
                        .param("lon", "17.0658")
                        .param("radius", "50000"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(1));
    }

    @Test
    void nearby_rejectsOutOfRangeCoordinates() throws Exception {
        mockMvc.perform(get("/api/tours/nearby")
                        .param("lat", "-122.0")
                        .param("lon", "17.0658")
                        .param("radius", "50000"))
                .andExpect(status().isBadRequest());

        verifyNoInteractions(tourService);
    }
}
