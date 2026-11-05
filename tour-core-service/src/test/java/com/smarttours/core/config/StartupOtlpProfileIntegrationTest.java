package com.smarttours.core.config;

import com.smarttours.core.support.IntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.boot.micrometer.tracing.test.autoconfigure.AutoConfigureTracing;
import org.springframework.context.ApplicationContext;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.assertThat;

/** The application as the observability profile runs it: exporters created, with nobody listening yet. */
@AutoConfigureTracing
@ActiveProfiles("otlp")
@TestPropertySource(properties = "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:1")
class StartupOtlpProfileIntegrationTest extends IntegrationTest {

    @Autowired
    private ApplicationContext context;

    @Test
    void itStartsWithExportersOn() {
        assertThat(context.getEnvironment().getProperty("management.tracing.export.enabled")).isEqualTo("true");
    }
}
