package com.smarttours.core.fx;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class RateSanityCheckTest {

    private static final LocalDate TODAY = LocalDate.parse("2026-10-31");

    private final RateSanityCheck check = new RateSanityCheck(FxTestData.properties());

    @Test
    void realRatesPassAgainstThemselvesAndWithNothingInUse() {
        assertThat(check.check(FxTestData.fetched(TODAY), Optional.empty(), TODAY)).isEmpty();
        assertThat(check.check(FxTestData.fetched(TODAY), Optional.of(FxTestData.snapshot(TODAY.minusDays(1))), TODAY))
                .isEmpty();
    }

    /**
     * Banco Nacional de Angola's NAD rates through Frankfurter on 2026-10-30
     * (fixtures/frankfurter/rates-bna-peg-off.json): NAD/ZAR 0.97847, 2.2% off a legal 1:1 peg.
     */
    @Test
    void aRateThatBreaksThePegIsRejected() {
        FetchedRates bna = new FetchedRates("NAD", "frankfurter:BNA", List.of(
                rate("BWP", "0.83079"), rate("EUR", "0.052356"), rate("GBP", "0.04467"),
                rate("USD", "0.059983"), rate("ZAR", "0.97847")));

        assertThat(check.check(bna, Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason, RateSanityCheck.Violation::message)
                .containsExactly(org.assertj.core.groups.Tuple.tuple(RateSanityCheck.Reason.PEG_VIOLATION,
                        "NAD/ZAR is 0.97847, more than 0.5% from the 1:1 peg"));
    }

    @Test
    void thePegToleranceIsInclusive() {
        assertThat(check.check(with("ZAR", "1.005"), Optional.empty(), TODAY)).isEmpty();
        assertThat(check.check(with("ZAR", "0.995"), Optional.empty(), TODAY)).isEmpty();
        assertThat(check.check(with("ZAR", "1.00501"), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsExactly(RateSanityCheck.Reason.PEG_VIOLATION);
    }

    /** A decimal slip (0.53827 for 0.053827) is a 900% move. */
    @Test
    void aQuoteThatMovedTooFarIsRejected() {
        assertThat(check.check(with("EUR", "0.53827"), Optional.of(FxTestData.snapshot(TODAY)), TODAY))
                .extracting(RateSanityCheck.Violation::reason, RateSanityCheck.Violation::message)
                .containsExactly(org.assertj.core.groups.Tuple.tuple(RateSanityCheck.Reason.MOVE_TOO_LARGE,
                        "EUR moved 900.00% (0.053827 to 0.53827), limit 10%"));
    }

    @Test
    void aMoveJustUnderTheLimitIsAccepted() {
        // 0.053827 * 1.0999 = 0.0592043...
        assertThat(check.check(with("EUR", "0.0592043"), Optional.of(FxTestData.snapshot(TODAY)), TODAY)).isEmpty();
        assertThat(check.check(with("EUR", "0.0592150"), Optional.of(FxTestData.snapshot(TODAY)), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsExactly(RateSanityCheck.Reason.MOVE_TOO_LARGE);
    }

    @Test
    void zeroNegativeMissingDuplicateAndFutureRatesAreRejected() {
        assertThat(check.check(with("USD", "0"), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsExactly(RateSanityCheck.Reason.NON_POSITIVE);
        assertThat(check.check(with("USD", "-0.06"), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsExactly(RateSanityCheck.Reason.NON_POSITIVE);

        List<FetchedRates.Rate> withoutGbp = new ArrayList<>(FxTestData.fetched(TODAY).rates());
        withoutGbp.removeIf(rate -> rate.quote().equals("GBP"));
        assertThat(check.check(new FetchedRates("NAD", "test", withoutGbp), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::message).containsExactly("GBP is missing");

        List<FetchedRates.Rate> twice = new ArrayList<>(FxTestData.fetched(TODAY).rates());
        twice.add(rate("EUR", "0.06"));
        assertThat(check.check(new FetchedRates("NAD", "test", twice), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsExactly(RateSanityCheck.Reason.DUPLICATE_QUOTE);

        assertThat(check.check(FxTestData.fetched(TODAY.plusDays(2)), Optional.empty(), TODAY))
                .extracting(RateSanityCheck.Violation::reason).containsOnly(RateSanityCheck.Reason.FUTURE_DATE);
    }

    private static FetchedRates with(String quote, String value) {
        List<FetchedRates.Rate> rates = new ArrayList<>(FxTestData.fetched(TODAY).rates());
        rates.replaceAll(rate -> rate.quote().equals(quote) ? rate(quote, value) : rate);
        return new FetchedRates("NAD", "test", rates);
    }

    private static FetchedRates.Rate rate(String quote, String value) {
        return new FetchedRates.Rate(quote, new BigDecimal(value), TODAY);
    }
}
