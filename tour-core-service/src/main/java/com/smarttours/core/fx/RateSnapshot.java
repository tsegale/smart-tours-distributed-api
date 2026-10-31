package com.smarttours.core.fx;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Map;
import java.util.Optional;

/**
 * The accepted rates currently in use: per quote, the newest stored rate. Quotes may carry different
 * dates (each central bank publishes on its own calendar), so staleness is judged per quote.
 * Also the value cached in Redis and in-process.
 *
 * @param fetchedAt when the newest of these rows was fetched
 */
public record RateSnapshot(String base, Map<String, Quote> quotes, Instant fetchedAt) {

    public record Quote(BigDecimal rate, LocalDate asOf, String source) {
    }

    public RateSnapshot {
        quotes = Map.copyOf(quotes);
    }

    public Optional<Quote> quote(String currency) {
        return Optional.ofNullable(quotes.get(currency));
    }

    public boolean isEmpty() {
        return quotes.isEmpty();
    }
}
