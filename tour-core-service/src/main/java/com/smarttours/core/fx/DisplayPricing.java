package com.smarttours.core.fx;

import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Currency;
import java.util.Locale;
import java.util.Optional;

/**
 * Turns {@code ?currency=} into the {@link CurrencyConversion} for one request (ADR 0011). Reads rates only
 * from {@link ExchangeRates} (memory, Redis, database), never from a provider, so it costs microseconds.
 *
 * <p>Staleness is the age in days of the quote's publication date: up to {@code fresh-days} (4, which covers a
 * long weekend of a central bank that publishes on business days only) it is served as fresh, up to
 * {@code max-age-days} (14) as {@code stale: true}, and beyond that it is not used: the client gets the
 * base price and the reason.
 */
@Service
public class DisplayPricing {

    private final ExchangeRates rates;
    private final FxProperties properties;
    private final Clock clock;

    public DisplayPricing(ExchangeRates rates, FxProperties properties, Clock clock) {
        this.rates = rates;
        this.properties = properties;
        this.clock = clock;
    }

    public CurrencyConversion forCurrency(String requested) {
        if (requested == null || requested.isBlank()) {
            return CurrencyConversion.none();
        }
        String code = requested.trim().toUpperCase(Locale.ROOT);
        if (!properties.displayCurrencies().contains(code)) {
            throw new UnsupportedCurrencyException(requested, properties.displayCurrencies());
        }
        Currency target = Currency.getInstance(code);
        if (code.equals(properties.base())) {
            return CurrencyConversion.identity(target);
        }

        Optional<RateSnapshot.Quote> quote = rates.current().flatMap(snapshot -> snapshot.quote(code));
        if (quote.isEmpty()) {
            return CurrencyConversion.unavailable(target, "No exchange rate for " + code + " has been loaded yet");
        }
        long ageDays = ChronoUnit.DAYS.between(quote.get().asOf(), LocalDate.now(clock.withZone(properties.zone())));
        if (ageDays > properties.maxAgeDays()) {
            return CurrencyConversion.unavailable(target, "The newest %s rate is from %s, %d days old; rates older than %d days are not used"
                    .formatted(code, quote.get().asOf(), ageDays, properties.maxAgeDays()));
        }
        return CurrencyConversion.at(target, quote.get().rate(), quote.get().asOf(), ageDays > properties.freshDays());
    }

    public String baseCurrency() {
        return properties.base();
    }
}
