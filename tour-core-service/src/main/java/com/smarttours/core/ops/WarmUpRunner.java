package com.smarttours.core.ops;

import com.smarttours.core.weather.WeatherProperties;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.boot.web.server.context.WebServerApplicationContext;
import org.springframework.context.ApplicationContext;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.http.HttpClient;
import java.time.Clock;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Sends the service's own hot requests to itself before it reports ready, so the first visitors don't pay for
 * class loading, lazy initialisation, the first TLS handshake with Open-Meteo, the Redis and database
 * connections, the exchange-rate snapshot and the Jackson serialisers.
 *
 * <p>Requests go over HTTP to the real port, so they cross the same filters, controllers, caches and resilience
 * policies as traffic. Spring Boot publishes {@link ReadinessState#ACCEPTING_TRAFFIC} only after every
 * {@link ApplicationRunner} has returned, so the readiness probe (and with it the compose healthcheck the gateway
 * waits for) stays down until this is done. A failed request is logged and the warm-up goes on: warming up must
 * never be the reason a service can't start.
 */
@Component
public class WarmUpRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(WarmUpRunner.class);
    private static final int SAMPLE_TOURS = 3;

    private final ApplicationContext context;
    private final ApplicationAvailability availability;
    private final JdbcClient jdbc;
    private final WarmUpProperties properties;
    private final WeatherProperties weather;
    private final Clock clock;
    private final MeterRegistry meters;
    private final RestClient.Builder restClients;
    private volatile Result lastResult;

    public WarmUpRunner(ApplicationContext context, ApplicationAvailability availability, JdbcClient jdbc,
                        WarmUpProperties properties, WeatherProperties weather, Clock clock, MeterRegistry meters,
                        RestClient.Builder restClients) {
        this.context = context;
        this.availability = availability;
        this.jdbc = jdbc;
        this.properties = properties;
        this.weather = weather;
        this.clock = clock;
        this.meters = meters;
        this.restClients = restClients;
    }

    /** What the last warm-up did; null if it didn't run. */
    public Result lastResult() {
        return lastResult;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!properties.enabled()) {
            log.info("Warm-up disabled");
            return;
        }
        if (!(context instanceof WebServerApplicationContext web) || web.getWebServer() == null) {
            log.info("Warm-up skipped: no web server to send requests to");
            return;
        }
        ReadinessState readiness = availability.getReadinessState();
        RestClient client = client(web.getWebServer().getPort());
        List<String> paths = paths();
        long started = System.nanoTime();
        long deadline = started + properties.timeout().toNanos();
        int sent = 0;
        int failed = 0;
        rounds:
        for (int round = 0; round < properties.rounds(); round++) {
            for (String path : paths) {
                if (System.nanoTime() > deadline) {
                    log.warn("Warm-up stopped at its {} limit after {} requests", properties.timeout(), sent);
                    break rounds;
                }
                sent++;
                try {
                    client.get().uri(path).retrieve().toBodilessEntity();
                } catch (RestClientException ex) {
                    failed++;
                    log.warn("Warm-up request {} failed: {}", path, ex.getMessage());
                }
            }
        }
        long elapsed = System.nanoTime() - started;
        Timer.builder("warmup.duration").description("Warm-up before readiness").register(meters)
                .record(elapsed, TimeUnit.NANOSECONDS);
        lastResult = new Result(readiness, sent, failed, TimeUnit.NANOSECONDS.toMillis(elapsed));
        log.info("Warm-up finished in {} ms: {} requests, {} failed", lastResult.millis(), sent, failed);
    }

    private RestClient client(int port) {
        HttpClient http = HttpClient.newBuilder().connectTimeout(properties.requestTimeout()).build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(properties.requestTimeout());
        return restClients.clone().baseUrl("http://127.0.0.1:" + port).requestFactory(factory).build();
    }

    /**
     * The hot paths: departure weather (forecast and climate normal, which opens the outbound connections), tour
     * detail with a display currency and weather, the plain detail, the list and two searches. Several tours, so
     * cache misses run as well as hits.
     */
    private List<String> paths() {
        List<Long> ids = jdbc.sql("SELECT id FROM tours ORDER BY id LIMIT :n").param("n", SAMPLE_TOURS)
                .query(Long.class).list();
        LocalDate today = LocalDate.now(clock.withZone(weather.zone()));
        LocalDate forecastDay = today.plusDays(5);
        LocalDate climateDay = today.plusDays(weather.forecastHorizonDays() + 30L);
        List<String> paths = new ArrayList<>();
        for (Long id : ids) {
            paths.add("/api/tours/" + id + "/weather?date=" + forecastDay);
            paths.add("/api/tours/" + id + "/weather?date=" + climateDay);
            paths.add("/api/tours/" + id + "?currency=EUR&include=weather&date=" + forecastDay);
            paths.add("/api/tours/" + id);
        }
        paths.add("/api/tours?page=0&size=20&currency=EUR");
        paths.add("/api/tours/search?q=tour&currency=EUR");
        paths.add("/api/tours/search?lat=-22.56&lon=17.08&radius=200000");
        return paths;
    }

    /**
     * @param readinessDuringWarmUp the readiness state while the warm-up ran: refusing traffic, by design
     * @param requests              warm-up requests sent
     * @param failed                of which failed
     * @param millis                how long it took
     */
    public record Result(ReadinessState readinessDuringWarmUp, int requests, int failed, long millis) {
    }
}
