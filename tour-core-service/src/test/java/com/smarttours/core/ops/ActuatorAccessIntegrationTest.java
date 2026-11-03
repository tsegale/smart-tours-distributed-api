package com.smarttours.core.ops;

import com.smarttours.core.support.ProviderStubsIntegrationTest;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** Actuator endpoints are read-only: state can be inspected, never changed. */
class ActuatorAccessIntegrationTest extends ProviderStubsIntegrationTest {

    private static final String BREAKER = "open-meteo-forecast";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private CircuitBreakerRegistry circuitBreakers;

    @Test
    void circuitBreakerStateCanBeReadButNotChanged() throws Exception {
        mockMvc.perform(get("/actuator/circuitbreakers"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.circuitBreakers['" + BREAKER + "'].state").value("CLOSED"));

        mockMvc.perform(post("/actuator/circuitbreakers/{name}", BREAKER)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"updateState\":\"FORCE_OPEN\"}"))
                .andExpect(status().is4xxClientError());

        assertThat(circuitBreakers.circuitBreaker(BREAKER).getState()).isEqualTo(CircuitBreaker.State.CLOSED);
    }
}
