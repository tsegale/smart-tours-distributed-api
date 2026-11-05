package com.smarttours.gateway;

import io.micrometer.observation.ObservationPredicate;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.instrumentation.logback.appender.v1_0.OpenTelemetryAppender;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.server.reactive.observation.ServerRequestObservationContext;

/** OpenTelemetry beyond what Spring Boot configures (ADR 0017). */
@Configuration
public class TelemetryConfig {

    /**
     * Health probes would bury every real trace, and each request would otherwise get a dozen spans from Spring
     * Security's filter chain.
     */
    @Bean
    ObservationPredicate noTracesForProbesOrSecurityFilters() {
        return (name, context) -> {
            if (name.startsWith("spring.security.")) {
                return false;
            }
            return !(context instanceof ServerRequestObservationContext request)
                    || !request.getCarrier().getPath().value().startsWith("/actuator");
        };
    }

    /** Spring Boot provides the SDK but not the Logback appender: it has to be told which SDK to use. */
    @Bean
    InitializingBean installLogExport(OpenTelemetry openTelemetry) {
        return () -> OpenTelemetryAppender.install(openTelemetry);
    }
}
