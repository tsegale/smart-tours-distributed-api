package com.smarttours.core.fx;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Currency;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CurrencyConversionTest {

    private static final LocalDate AS_OF = LocalDate.parse("2026-10-30");

    /**
     * HALF_EVEN to the currency's ISO 4217 minor units. Ties (exactly half a minor unit) go to the even
     * neighbour; JPY has no minor unit and BHD three, neither offered but both must round right.
     */
    @ParameterizedTest(name = "{0} NAD x {1} = {3} {2}")
    @CsvSource({
            "1850.00, 0.053827,   EUR, 99.58",     // 99.57995
            "1850.00, 0.060149,   USD, 111.28",    // 111.27565
            "1850.00, 0.044991,   GBP, 83.23",     // 83.23335
            "1850.00, 0.81569,    BWP, 1509.03",   // 1509.0265
            "1850.00, 1.0005,     ZAR, 1850.92",   // 1850.925: a tie, stays on the even 2
            "1850.10, 1.0005,     ZAR, 1851.03",   // 1851.02505: just above the tie
            "100.00,  0.00125,    EUR, 0.12",      // 0.125: a tie, rounds down to the even 2
            "100.00,  0.00135,    EUR, 0.14",      // 0.135: a tie, rounds up to the even 4
            "1850.00, 8.4321,     JPY, 15599",     // 15599.385, no decimals
            "100.00,  1.025,      JPY, 102",       // 102.5: a tie, 2 is even
            "100.00,  1.035,      JPY, 104",       // 103.5: a tie, 4 is even
            "1850.00, 0.0205,     BHD, 37.925",    // three decimals
            "0.00,    0.053827,   EUR, 0.00",
    })
    void roundsHalfEvenToTheMinorUnitsOfTheTarget(String nad, String rate, String currency, String expected) {
        DisplayPrice price = CurrencyConversion.at(Currency.getInstance(currency), new BigDecimal(rate), AS_OF, false)
                .display(new BigDecimal(nad));

        assertThat(price.amount()).isEqualByComparingTo(expected);
        assertThat(price.amount().scale()).isEqualTo(Currency.getInstance(currency).getDefaultFractionDigits());
        assertThat(price.currency()).isEqualTo(currency);
        assertThat(price.rateAsOf()).isEqualTo(AS_OF);
    }

    @Test
    void rateIsReportedWithoutTheStoredTrailingZeros() {
        DisplayPrice price = CurrencyConversion.at(Currency.getInstance("EUR"), new BigDecimal("0.05382700"), AS_OF, true)
                .display(new BigDecimal("1850.00"));

        assertThat(price.rate().toPlainString()).isEqualTo("0.053827");
        assertThat(price.stale()).isTrue();
    }

    @Test
    void baseCurrencyIsTheSameAmountAtRateOne() {
        DisplayPrice price = CurrencyConversion.identity(Currency.getInstance("NAD")).display(new BigDecimal("1850.00"));

        assertThat(price.amount()).isEqualByComparingTo("1850.00");
        assertThat(price.rate()).isEqualByComparingTo("1");
        assertThat(price.rateAsOf()).isNull();
        assertThat(price.stale()).isFalse();
    }

    @Test
    void noneAndUnavailableGiveNoDisplayPrice() {
        assertThat(CurrencyConversion.none().display(BigDecimal.TEN)).isNull();
        assertThat(CurrencyConversion.none().changesRepresentation()).isFalse();

        CurrencyConversion unavailable = CurrencyConversion.unavailable(Currency.getInstance("EUR"), "too old");
        assertThat(unavailable.display(BigDecimal.TEN)).isNull();
        assertThat(unavailable.unavailableReason()).isEqualTo("too old");
        assertThat(unavailable.changesRepresentation()).isTrue();
    }

    @Test
    void priceBoundsInTheDisplayCurrencyAreWidenedToTheCentInNad() {
        CurrencyConversion eur = CurrencyConversion.at(Currency.getInstance("EUR"), new BigDecimal("0.053827"), AS_OF, false);

        // 100 EUR / 0.053827 = 1857.80741... NAD
        assertThat(eur.toBase(new BigDecimal("100"), RoundingMode.FLOOR)).isEqualByComparingTo("1857.80");
        assertThat(eur.toBase(new BigDecimal("100"), RoundingMode.CEILING)).isEqualByComparingTo("1857.81");
        assertThat(CurrencyConversion.none().toBase(new BigDecimal("100"), RoundingMode.FLOOR)).isEqualByComparingTo("100");
    }

    @Test
    void priceBoundsWithoutAUsableRateCannotBeConverted() {
        CurrencyConversion unavailable = CurrencyConversion.unavailable(Currency.getInstance("EUR"), "too old");

        assertThatThrownBy(() -> unavailable.toBase(BigDecimal.TEN, RoundingMode.FLOOR))
                .isInstanceOf(ExchangeRatesUnavailableException.class)
                .hasMessage("too old");
    }
}
