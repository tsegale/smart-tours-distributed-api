package com.smarttours.core.fx;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.LocalDate;
import java.time.ZonedDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class DisplayPricingTest {

    /** Saturday 31 October 2026, 23:30 in Windhoek, already 1 November in UTC+3 and later: "today" is Windhoek's. */
    private static final Clock CLOCK = Clock.fixed(
            ZonedDateTime.of(2026, 10, 31, 23, 30, 0, 0, FxTestData.WINDHOEK).toInstant(), FxTestData.WINDHOEK);
    private static final LocalDate TODAY = LocalDate.parse("2026-10-31");

    private final ExchangeRates rates = mock(ExchangeRates.class);
    private final DisplayPricing pricing = new DisplayPricing(rates, FxTestData.properties(), CLOCK);

    /** Fresh up to 4 days, stale up to 14, unusable after: both boundaries, from both sides. */
    @ParameterizedTest(name = "{0} days old: usable={1}, stale={2}")
    @CsvSource({
            "0,  true,  false",
            "4,  true,  false",
            "5,  true,  true",
            "14, true,  true",
            "15, false, false",
    })
    void staleness(int ageDays, boolean usable, boolean stale) {
        when(rates.current()).thenReturn(Optional.of(FxTestData.snapshot(TODAY.minusDays(ageDays))));

        CurrencyConversion conversion = pricing.forCurrency("EUR");
        DisplayPrice price = conversion.display(new BigDecimal("1850.00"));

        if (usable) {
            assertThat(price).isNotNull();
            assertThat(price.stale()).isEqualTo(stale);
            assertThat(price.rateAsOf()).isEqualTo(TODAY.minusDays(ageDays));
            assertThat(conversion.unavailableReason()).isNull();
        } else {
            assertThat(price).isNull();
            assertThat(conversion.unavailableReason())
                    .isEqualTo("The newest EUR rate is from 2026-10-16, 15 days old; rates older than 14 days are not used");
        }
    }

    @Test
    void currencyIsCaseInsensitive() {
        when(rates.current()).thenReturn(Optional.of(FxTestData.snapshot(TODAY)));

        assertThat(pricing.forCurrency(" gbp ").display(new BigDecimal("1850.00")).currency()).isEqualTo("GBP");
    }

    @Test
    void baseCurrencyNeedsNoRates() {
        DisplayPrice price = pricing.forCurrency("NAD").display(new BigDecimal("1850.00"));

        assertThat(price.amount()).isEqualByComparingTo("1850.00");
        assertThat(price.currency()).isEqualTo("NAD");
    }

    @Test
    void noRatesYetMeansNoDisplayPriceAndAReason() {
        when(rates.current()).thenReturn(Optional.empty());

        CurrencyConversion conversion = pricing.forCurrency("USD");

        assertThat(conversion.display(BigDecimal.TEN)).isNull();
        assertThat(conversion.unavailableReason()).isEqualTo("No exchange rate for USD has been loaded yet");
    }

    @Test
    void noCurrencyMeansNoConversion() {
        assertThat(pricing.forCurrency(null).changesRepresentation()).isFalse();
        assertThat(pricing.forCurrency(" ").changesRepresentation()).isFalse();
    }

    @Test
    void unsupportedCurrencyListsTheSupportedOnes() {
        assertThatThrownBy(() -> pricing.forCurrency("JPY"))
                .isInstanceOf(UnsupportedCurrencyException.class)
                .hasMessage("Currency 'JPY' is not supported; use one of NAD, ZAR, USD, EUR, GBP, BWP")
                .extracting(ex -> ((UnsupportedCurrencyException) ex).getSupported())
                .asList().containsExactly("NAD", "ZAR", "USD", "EUR", "GBP", "BWP");
        assertThatThrownBy(() -> pricing.forCurrency("EURO")).isInstanceOf(UnsupportedCurrencyException.class);
    }
}
