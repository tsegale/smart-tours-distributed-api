package com.smarttours.core.tour;

import com.smarttours.core.support.TourFixtures;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourRequest;
import com.smarttours.core.tour.dto.TourResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(TourController.class)
class TourControllerTest {

    static final TourResponse SOSSUSVLEI = new TourResponse(
            1L, "Sossusvlei Sunrise Dunes", "Climb Dune 45 at dawn", new BigDecimal("1850.00"), "NAD",
            -24.7275, 15.3424, Category.DESERT, Difficulty.MODERATE, 300, new BigDecimal("4.60"), 12,
            Instant.parse("2026-10-01T08:00:00Z"));

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private TourService tourService;

    @Test
    void create_returns201WithLocationAndEtag() throws Exception {
        when(tourService.create(any(TourRequest.class))).thenReturn(new VersionedTour(SOSSUSVLEI, 0));

        mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON).content(TourFixtures.VALID_BODY))
                .andExpect(status().isCreated())
                .andExpect(header().string("Location", "http://localhost/api/tours/1"))
                .andExpect(header().string(HttpHeaders.ETAG, "\"0\""))
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.latitude").value(-24.7275))
                .andExpect(jsonPath("$.longitude").value(15.3424))
                .andExpect(jsonPath("$.category").value("DESERT"))
                .andExpect(jsonPath("$.durationMinutes").value(300))
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
    void create_rejectsUnknownCategory() throws Exception {
        mockMvc.perform(post("/api/tours")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("DESERT", "SPACE")))
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
    void get_found_returnsTourWithEtagAndCacheControl() throws Exception {
        when(tourService.get(1L)).thenReturn(new VersionedTour(SOSSUSVLEI, 7));

        mockMvc.perform(get("/api/tours/1"))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ETAG, "\"7\""))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, "max-age=60, public"))
                .andExpect(jsonPath("$.name").value("Sossusvlei Sunrise Dunes"))
                .andExpect(jsonPath("$.avgRating").value(4.6));
    }

    @Test
    void get_withMatchingIfNoneMatch_returns304WithoutBody() throws Exception {
        when(tourService.get(1L)).thenReturn(new VersionedTour(SOSSUSVLEI, 7));

        mockMvc.perform(get("/api/tours/1").header(HttpHeaders.IF_NONE_MATCH, "\"7\""))
                .andExpect(status().isNotModified())
                .andExpect(header().string(HttpHeaders.ETAG, "\"7\""))
                .andExpect(content().string(""));
    }

    @Test
    void get_withStaleIfNoneMatch_returns200() throws Exception {
        when(tourService.get(1L)).thenReturn(new VersionedTour(SOSSUSVLEI, 8));

        mockMvc.perform(get("/api/tours/1").header(HttpHeaders.IF_NONE_MATCH, "\"7\""))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ETAG, "\"8\""));
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
    void put_withoutIfMatch_returns428() throws Exception {
        when(tourService.update(eq(1L), any(TourRequest.class), isNull()))
                .thenThrow(new PreconditionRequiredException());

        mockMvc.perform(put("/api/tours/1").contentType(MediaType.APPLICATION_JSON).content(TourFixtures.VALID_BODY))
                .andExpect(status().isPreconditionRequired())
                .andExpect(header().string("Content-Type", MediaType.APPLICATION_PROBLEM_JSON_VALUE))
                .andExpect(jsonPath("$.type").value("https://smarttours.dev/problems/precondition-required"));
    }

    @Test
    void put_withStaleIfMatch_returns412WithCurrentEtag() throws Exception {
        when(tourService.update(eq(1L), any(TourRequest.class), eq("\"3\"")))
                .thenThrow(new PreconditionFailedException(1L, "\"4\""));

        mockMvc.perform(put("/api/tours/1").header(HttpHeaders.IF_MATCH, "\"3\"")
                        .contentType(MediaType.APPLICATION_JSON).content(TourFixtures.VALID_BODY))
                .andExpect(status().isPreconditionFailed())
                .andExpect(header().string(HttpHeaders.ETAG, "\"4\""))
                .andExpect(header().string("Content-Type", MediaType.APPLICATION_PROBLEM_JSON_VALUE))
                .andExpect(jsonPath("$.tourId").value(1));
    }

    @Test
    void put_valid_returnsNewEtag() throws Exception {
        when(tourService.update(anyLong(), any(TourRequest.class), eq("\"3\"")))
                .thenReturn(new VersionedTour(SOSSUSVLEI, 4));

        mockMvc.perform(put("/api/tours/1").header(HttpHeaders.IF_MATCH, "\"3\"")
                        .contentType(MediaType.APPLICATION_JSON).content(TourFixtures.VALID_BODY))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ETAG, "\"4\""));
    }

    @Test
    void put_invalidBody_isRejectedBeforeThePreconditionCheck() throws Exception {
        mockMvc.perform(put("/api/tours/1").header(HttpHeaders.IF_MATCH, "\"3\"")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"name\": \"\"}"))
                .andExpect(status().isBadRequest());

        verifyNoInteractions(tourService);
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
