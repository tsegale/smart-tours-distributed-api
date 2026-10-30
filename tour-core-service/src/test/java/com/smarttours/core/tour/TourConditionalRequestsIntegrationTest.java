package com.smarttours.core.tour;

import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.support.TourFixtures;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** ETag, If-Match and If-None-Match end to end: controller, service, JPA versioning and the real database. */
@AutoConfigureMockMvc
class TourConditionalRequestsIntegrationTest extends IntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    private String location;
    private String etag;

    @BeforeEach
    void createTour() throws Exception {
        MvcResult created = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY))
                .andExpect(status().isCreated())
                .andReturn();
        location = created.getResponse().getHeader(HttpHeaders.LOCATION);
        etag = created.getResponse().getHeader(HttpHeaders.ETAG);
        clearInvocations(rabbitTemplate);
    }

    @Test
    void get_revalidatesWithIfNoneMatch() throws Exception {
        mockMvc.perform(get(location))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ETAG, etag))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, "max-age=60, public"));

        mockMvc.perform(get(location).header(HttpHeaders.IF_NONE_MATCH, etag))
                .andExpect(status().isNotModified());
    }

    @Test
    void put_requiresIfMatch() throws Exception {
        mockMvc.perform(put(location).contentType(MediaType.APPLICATION_JSON).content(renamed("No precondition")))
                .andExpect(status().isPreconditionRequired());

        mockMvc.perform(get(location)).andExpect(jsonPath("$.name").value("Sossusvlei Sunrise Dunes"));
        verify(rabbitTemplate, never()).convertAndSend(any(String.class), any(String.class), any(Object.class));
    }

    @Test
    void put_withCurrentEtag_updatesBumpsTheEtagAndPublishesAfterCommit() throws Exception {
        String newEtag = mockMvc.perform(put(location).header(HttpHeaders.IF_MATCH, etag)
                        .contentType(MediaType.APPLICATION_JSON).content(renamed("Sossusvlei at Dawn")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("Sossusvlei at Dawn"))
                .andReturn().getResponse().getHeader(HttpHeaders.ETAG);

        assertThat(newEtag).isNotEqualTo(etag);
        mockMvc.perform(get(location))
                .andExpect(header().string(HttpHeaders.ETAG, newEtag))
                .andExpect(jsonPath("$.name").value("Sossusvlei at Dawn"));
        // The old ETag no longer revalidates.
        mockMvc.perform(get(location).header(HttpHeaders.IF_NONE_MATCH, etag)).andExpect(status().isOk());
        verify(rabbitTemplate).convertAndSend(eq("smarttours.events"), eq("tour.updated"), any(Object.class));
    }

    @Test
    void put_lostUpdate_isRefusedWith412() throws Exception {
        // Two operators read the same version. The first write wins; the second must not overwrite it.
        mockMvc.perform(put(location).header(HttpHeaders.IF_MATCH, etag)
                        .contentType(MediaType.APPLICATION_JSON).content(renamed("First operator")))
                .andExpect(status().isOk());

        mockMvc.perform(put(location).header(HttpHeaders.IF_MATCH, etag)
                        .contentType(MediaType.APPLICATION_JSON).content(renamed("Second operator")))
                .andExpect(status().isPreconditionFailed())
                .andExpect(header().exists(HttpHeaders.ETAG))
                .andExpect(jsonPath("$.type").value("https://smarttours.dev/problems/precondition-failed"));

        mockMvc.perform(get(location)).andExpect(jsonPath("$.name").value("First operator"));
    }

    @Test
    void put_unknownTour_returns404() throws Exception {
        mockMvc.perform(put("/api/tours/999999").header(HttpHeaders.IF_MATCH, "\"0\"")
                        .contentType(MediaType.APPLICATION_JSON).content(renamed("Ghost")))
                .andExpect(status().isNotFound());
    }

    private static String renamed(String name) {
        return TourFixtures.VALID_BODY.replace("Sossusvlei Sunrise Dunes", name);
    }
}
