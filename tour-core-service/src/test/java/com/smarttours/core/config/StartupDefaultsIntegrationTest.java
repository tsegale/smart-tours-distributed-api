package com.smarttours.core.config;

import com.smarttours.core.outbox.TraceParents;
import com.smarttours.core.support.IntegrationTest;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.tracing.test.autoconfigure.AutoConfigureTracing;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The application as the default compose profile runs it. Spring's test support turns tracing off unless told otherwise,
 * which once hid a crash: compose passes an empty OTLP endpoint, and every other test here still passed.
 */
@AutoConfigureTracing
@TestPropertySource(properties = "OTEL_EXPORTER_OTLP_ENDPOINT=")
class StartupDefaultsIntegrationTest extends IntegrationTest {

    @Autowired
    private Tracer tracer;

    @Autowired
    private TraceParents traces;

    @Test
    void itStartsWithAnEmptyEndpointAndStoresNoTraceContext() {
        Span span = tracer.nextSpan().name("a request").start();
        try (Tracer.SpanInScope ignored = tracer.withSpan(span)) {
            assertThat(traces.current()).as("nothing is sampled, so there is no trace to continue").isNull();
        } finally {
            span.end();
        }
    }
}
