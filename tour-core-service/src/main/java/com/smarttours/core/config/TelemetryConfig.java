package com.smarttours.core.config;

import io.micrometer.observation.ObservationPredicate;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.instrumentation.logback.appender.v1_0.OpenTelemetryAppender;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.server.observation.ServerRequestObservationContext;

/** OpenTelemetry beyond what Spring Boot configures (ADR 0017). */
@Configuration
public class TelemetryConfig {

    /**
     * Polling loops and health probes would bury every real trace: the outbox relay alone would make two traces a
     * second. A relayed event still gets its span, from {@code OutboxRelay}.
     */
    @Bean
    ObservationPredicate noTracesForPollingOrProbes() {
        return (name, context) -> {
            if (name.startsWith("tasks.scheduled")) {
                return false;
            }
            return !(context instanceof ServerRequestObservationContext request)
                    || !request.getCarrier().getRequestURI().startsWith("/actuator");
        };
    }

    /** Spring Boot provides the SDK but not the Logback appender: it has to be told which SDK to use. */
    @Bean
    InitializingBean installLogExport(OpenTelemetry openTelemetry) {
        return () -> OpenTelemetryAppender.install(openTelemetry);
    }
}
