package com.smarttours.core.fx;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * Fetches the latest rates, checks them and, if they pass, stores and publishes them. Any failure
 * (provider down, circuit open, a fetch that fails the sanity check) leaves the rates in use untouched:
 * they age, and {@link CurrencyConversion} marks them stale and eventually stops using them.
 *
 * <p>Metrics: {@code fx_refresh_total{outcome=accepted|rejected|failed}} and
 * {@code fx_refresh_rejections_total{reason}} per sanity-check violation.
 */
@Service
public class ExchangeRateRefresher {

    private static final Logger log = LoggerFactory.getLogger(ExchangeRateRefresher.class);

    public enum Outcome { ACCEPTED, REJECTED, FAILED }

    private final ExchangeRateProvider provider;
    private final RateSanityCheck sanityCheck;
    private final FxRateRepository repository;
    private final ExchangeRates rates;
    private final FxProperties properties;
    private final Clock clock;
    private final MeterRegistry meters;

    public ExchangeRateRefresher(ExchangeRateProvider provider, RateSanityCheck sanityCheck, FxRateRepository repository,
                                 ExchangeRates rates, FxProperties properties, Clock clock, MeterRegistry meters) {
        this.provider = provider;
        this.sanityCheck = sanityCheck;
        this.repository = repository;
        this.rates = rates;
        this.properties = properties;
        this.clock = clock;
        this.meters = meters;
    }

    public Outcome refresh() {
        FetchedRates fetched;
        try {
            fetched = provider.latest(properties.base(), properties.quotes());
        } catch (RuntimeException ex) {
            log.warn("Exchange-rate refresh failed, keeping the rates in use: {}", ex.toString());
            return outcome(Outcome.FAILED);
        }

        Optional<RateSnapshot> inUse = repository.latest(properties.base());
        List<RateSanityCheck.Violation> violations = sanityCheck.check(fetched, inUse, today());
        if (!violations.isEmpty()) {
            violations.forEach(violation -> Counter.builder("fx.refresh.rejections")
                    .description("Sanity-check violations that made a rate refresh be rejected")
                    .tag("reason", violation.reason().name().toLowerCase(Locale.ROOT))
                    .register(meters)
                    .increment());
            log.warn("Rejected exchange rates from {}, keeping the rates in use: {}", fetched.source(),
                    violations.stream().map(RateSanityCheck.Violation::message).toList());
            return outcome(Outcome.REJECTED);
        }

        Instant now = clock.instant();
        repository.save(fetched, now);
        repository.latest(properties.base()).ifPresent(rates::publish);
        log.info("Exchange rates refreshed from {}: {}", fetched.source(), fetched.rates());
        return outcome(Outcome.ACCEPTED);
    }

    /** Whether an accepted fetch happened today ({@code zone}), i.e. whether start-up may skip refreshing. */
    public boolean refreshedToday() {
        return repository.lastFetchedAt(properties.base())
                .map(at -> !at.atZone(properties.zone()).toLocalDate().isBefore(today()))
                .orElse(false);
    }

    private LocalDate today() {
        return LocalDate.now(clock.withZone(properties.zone()));
    }

    private Outcome outcome(Outcome outcome) {
        Counter.builder("fx.refresh")
                .description("Exchange-rate refreshes by outcome")
                .tag("outcome", outcome.name().toLowerCase(Locale.ROOT))
                .register(meters)
                .increment();
        return outcome;
    }
}
