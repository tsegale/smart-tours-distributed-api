package com.smarttours.core.ops;

import com.smarttours.core.support.ProviderStubsIntegrationTest;
import com.smarttours.core.support.TourFixtures;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** Each stage of tour detail is timed, and only tour detail feeds the request timer. */
class TourDetailStagesIntegrationTest extends ProviderStubsIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private MeterRegistry meters;

    @Test
    void tourDetailRecordsEveryStage() throws Exception {
        String location = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getHeader(HttpHeaders.LOCATION);
        long before = count("tour.detail.request", null);

        mockMvc.perform(get(location).param("currency", "NAD").param("include", "weather")
                        .param("date", LocalDate.now().plusDays(3).toString()))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/tours")).andExpect(status().isOk());

        assertThat(count("tour.detail.request", null)).isEqualTo(before + 1);
        for (String stage : new String[] {TourDetailStages.FX, TourDetailStages.WEATHER, TourDetailStages.WRITE}) {
            assertThat(count("tour.detail.stage", stage)).as(stage).isPositive();
        }
        assertThat(meters.find("catalogue.cache.read").tag("cache", "tour").timers())
                .extracting(Timer::count).anyMatch(n -> n > 0);
        assertThat(meters.find("catalogue.cache.load").tag("cache", "tour").timer()).isNotNull();
    }

    private long count(String name, String stage) {
        var search = meters.find(name);
        Timer timer = (stage == null ? search : search.tag("stage", stage)).timer();
        return timer == null ? 0 : timer.count();
    }
}
