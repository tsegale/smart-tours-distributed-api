package com.smarttours.core.fx;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.ZoneId;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Display-currency settings (ADR 0011).
 *
 * @param base               the currency prices are stored and charged in
 * @param displayCurrencies  what {@code ?currency=} accepts, base included
 * @param freshDays          rates up to this many days old are served with {@code stale=false}
 * @param maxAgeDays         up to this age with {@code stale=true}; older ones are not used at all
 * @param maxMovePercent     a fetched quote that moved more than this against the stored one is rejected
 * @param pegCurrency        currency the base is pegged 1:1 to
 * @param pegTolerancePercent how far base/peg may be from 1.0000 before a fetch is rejected
 * @param refreshCron        the daily refresh, in {@code zone}
 * @param refreshOnStartup   refresh at start-up when no rates were fetched today
 * @param localTtl           how long each instance keeps its in-process copy before re-reading Redis
 * @param zone               "today" for staleness and the refresh schedule
 */
@ConfigurationProperties("smarttours.fx")
public record FxProperties(
        String base,
        List<String> displayCurrencies,
        int freshDays,
        int maxAgeDays,
        BigDecimal maxMovePercent,
        String pegCurrency,
        BigDecimal pegTolerancePercent,
        String refreshCron,
        boolean refreshOnStartup,
        Duration localTtl,
        ZoneId zone) {

    public FxProperties {
        base = base.toUpperCase(Locale.ROOT);
        pegCurrency = pegCurrency.toUpperCase(Locale.ROOT);
        displayCurrencies = displayCurrencies.stream().map(c -> c.trim().toUpperCase(Locale.ROOT)).distinct().toList();
        if (!displayCurrencies.contains(base)) {
            throw new IllegalArgumentException("display-currencies must include the base currency " + base);
        }
        if (freshDays < 0 || maxAgeDays < freshDays) {
            throw new IllegalArgumentException("need 0 <= fresh-days <= max-age-days");
        }
    }

    /** The quotes to fetch: every display currency except the base, plus the peg currency for the peg check. */
    public Set<String> quotes() {
        Set<String> quotes = new LinkedHashSet<>(displayCurrencies);
        quotes.add(pegCurrency);
        quotes.remove(base);
        return quotes;
    }
}
