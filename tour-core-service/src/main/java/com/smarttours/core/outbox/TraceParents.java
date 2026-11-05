package com.smarttours.core.outbox;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.propagation.Propagator;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

/**
 * Keeps a trace going across the outbox (ADR 0017): the row stores the W3C traceparent of the transaction that wrote
 * it, and the relay, in another thread and maybe another process, publishes inside a span continuing that trace.
 */
@Component
public class TraceParents {

    public static final String HEADER = "traceparent";

    private final Tracer tracer;
    private final Propagator propagator;

    public TraceParents(Tracer tracer, Propagator propagator) {
        this.tracer = tracer;
        this.propagator = propagator;
    }

    /** The current span's traceparent, or null outside a sampled trace: with nothing exported, there's nothing to join. */
    public String current() {
        Span span = tracer.currentSpan();
        if (span == null || !Boolean.TRUE.equals(span.context().sampled())) {
            return null;
        }
        Map<String, String> carrier = new HashMap<>();
        propagator.inject(span.context(), carrier, Map::put);
        return carrier.get(HEADER);
    }

    /** A started producer span in the stored trace, or null when the row has none. The caller ends it. */
    public Span continueFrom(String traceparent, String name) {
        if (traceparent == null) {
            return null;
        }
        return propagator.extract(Map.of(HEADER, traceparent), (carrier, key) -> carrier.get(key))
                .name(name)
                .kind(Span.Kind.PRODUCER)
                .start();
    }

    public Tracer.SpanInScope inScope(Span span) {
        return tracer.withSpan(span);
    }
}
