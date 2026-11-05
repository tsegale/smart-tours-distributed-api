package com.smarttours.gateway;

import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.tracing.test.autoconfigure.AutoConfigureTracing;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.ActiveProfiles;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The gateway as compose runs it. Spring's test support turns tracing off unless told otherwise, which once hid a crash:
 * compose passes an empty OTLP endpoint, and every other test here still passed.
 */
class StartupProfilesTest {

    @Nested
    @AutoConfigureTracing
    @SpringBootTest(properties = {
            "smarttours.security.dev-tokens.enabled=true", "smarttours.security.jwt-secret=" + TicketRouteTest.SECRET,
            "OTEL_EXPORTER_OTLP_ENDPOINT=", "TOUR_SERVICE_URL=http://localhost:1",
            "BOOKING_SERVICE_URL=http://localhost:1", "AI_SERVICE_URL=http://localhost:1"
    })
    class TheDefaultProfile {

        @Autowired
        private ApplicationContext context;

        @Test
        void startsWithAnEmptyEndpointAndExportsNothing() {
            assertThat(context.getEnvironment().getProperty("management.tracing.export.enabled")).isEqualTo("false");
        }
    }

    @Nested
    @AutoConfigureTracing
    @ActiveProfiles("otlp")
    @SpringBootTest(properties = {
            "smarttours.security.dev-tokens.enabled=true", "smarttours.security.jwt-secret=" + TicketRouteTest.SECRET,
            "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:1", "TOUR_SERVICE_URL=http://localhost:1",
            "BOOKING_SERVICE_URL=http://localhost:1", "AI_SERVICE_URL=http://localhost:1"
    })
    class TheOtlpProfile {

        @Autowired
        private ApplicationContext context;

        @Test
        void startsWithExportersOn() {
            assertThat(context.getEnvironment().getProperty("management.tracing.export.enabled")).isEqualTo("true");
        }
    }
}
