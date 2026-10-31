package com.smarttours.core.fx;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

/** Settings and rates shared by the fx tests. Rates are the real ones of 2026-10-31 (fixtures/frankfurter). */
final class FxTestData {

    static final ZoneId WINDHOEK = ZoneId.of("Africa/Windhoek");

    static FxProperties properties() {
        return new FxProperties("NAD", List.of("NAD", "ZAR", "USD", "EUR", "GBP", "BWP"), 4, 14,
                new BigDecimal("10"), "ZAR", new BigDecimal("0.5"), "-", false, Duration.ofSeconds(60), WINDHOEK);
    }

    static FetchedRates fetched(LocalDate asOf) {
        return new FetchedRates("NAD", "frankfurter:blended", List.of(
                new FetchedRates.Rate("BWP", new BigDecimal("0.81569"), asOf),
                new FetchedRates.Rate("EUR", new BigDecimal("0.053827"), asOf),
                new FetchedRates.Rate("GBP", new BigDecimal("0.044991"), asOf),
                new FetchedRates.Rate("USD", new BigDecimal("0.060149"), asOf),
                new FetchedRates.Rate("ZAR", new BigDecimal("1.0005"), asOf)));
    }

    static RateSnapshot snapshot(LocalDate asOf) {
        return new RateSnapshot("NAD", Map.of(
                "BWP", quote("0.81569", asOf),
                "EUR", quote("0.053827", asOf),
                "GBP", quote("0.044991", asOf),
                "USD", quote("0.060149", asOf),
                "ZAR", quote("1.0005", asOf)), Instant.parse("2026-10-31T16:15:00Z"));
    }

    private static RateSnapshot.Quote quote(String rate, LocalDate asOf) {
        return new RateSnapshot.Quote(new BigDecimal(rate), asOf, "frankfurter:blended");
    }

    private FxTestData() {
    }
}
