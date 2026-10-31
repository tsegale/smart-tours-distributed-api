package com.smarttours.core.support;

import com.github.tomakehurst.wiremock.WireMockServer;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;

/**
 * Integration tests with every outbound provider pointed at one WireMock server (one Spring context shared by
 * all subclasses). Each test starts with no stubs, no recorded requests and every circuit closed.
 */
@AutoConfigureMockMvc
public abstract class ProviderStubsIntegrationTest extends IntegrationTest {

    protected static final WireMockServer PROVIDERS = new WireMockServer(options().dynamicPort());

    static {
        PROVIDERS.start();
    }

    @DynamicPropertySource
    static void providersOnWireMock(DynamicPropertyRegistry registry) {
        registry.add("smarttours.integrations.frankfurter.base-url", PROVIDERS::baseUrl);
        registry.add("smarttours.integrations.open-meteo-forecast.base-url", PROVIDERS::baseUrl);
        registry.add("smarttours.integrations.open-meteo-archive.base-url", PROVIDERS::baseUrl);
    }

    @Autowired
    private CircuitBreakerRegistry circuitBreakers;

    @BeforeEach
    void resetProviders() {
        PROVIDERS.resetAll();
        circuitBreakers.getAllCircuitBreakers().forEach(CircuitBreaker::reset);
    }
}
