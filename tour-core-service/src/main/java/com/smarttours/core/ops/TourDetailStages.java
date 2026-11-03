package com.smarttours.core.ops;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.function.Supplier;

/**
 * Where the time of {@code GET /api/tours/{id}} goes, one timer per stage ({@code tour.detail.stage}, tag
 * {@code stage}), plus the whole request as the server saw it ({@code tour.detail.request}). The tour lookup is
 * timed by the catalogue cache itself ({@code catalogue.cache.read} and {@code catalogue.cache.load}, tag
 * {@code cache=tour}). Histograms, so percentiles can be computed (and aggregated) from the scrape.
 *
 * <p>Whatever a client measures beyond {@code tour.detail.request} was spent outside this service's request
 * handling: the gateway, the network, or waiting for a Tomcat thread.
 */
@Component
public class TourDetailStages {

    public static final String FX = "fx";
    public static final String WEATHER = "weather";
    public static final String WRITE = "write";

    private final Timer fx;
    private final Timer weather;
    private final Timer write;
    private final Timer request;

    public TourDetailStages(MeterRegistry meters) {
        this.fx = stage(meters, FX, "Display-price conversion: rates snapshot and conversion");
        this.weather = stage(meters, WEATHER, "Departure weather: departure lookup, cache, provider wait");
        this.write = stage(meters, WRITE, "Response serialisation and write");
        this.request = histogram(Timer.builder("tour.detail.request")
                .description("GET /api/tours/{id}, from the first filter to the last byte handed to Tomcat"))
                .register(meters);
    }

    public <T> T fx(Supplier<T> work) {
        return fx.record(work);
    }

    public <T> T weather(Supplier<T> work) {
        return weather.record(work);
    }

    void recordWrite(long nanos) {
        write.record(Duration.ofNanos(nanos));
    }

    void recordRequest(long nanos) {
        request.record(Duration.ofNanos(nanos));
    }

    private static Timer stage(MeterRegistry meters, String stage, String description) {
        return histogram(Timer.builder("tour.detail.stage").tag("stage", stage).description(description))
                .register(meters);
    }

    public static Timer.Builder histogram(Timer.Builder builder) {
        return builder.publishPercentileHistogram()
                .minimumExpectedValue(Duration.ofMillis(1))
                .maximumExpectedValue(Duration.ofSeconds(30));
    }
}
