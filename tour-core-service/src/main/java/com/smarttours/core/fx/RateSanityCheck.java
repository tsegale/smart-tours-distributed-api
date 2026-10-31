package com.smarttours.core.fx;

import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Checks a fetch before it may replace the rates in use (ADR 0011). One bad answer from a provider must
 * not reprice the catalogue: a rejected fetch changes nothing, and the previous rates stay.
 * <ul>
 *   <li>every expected quote is there, exactly once, with a positive rate and a date not in the future;</li>
 *   <li>no quote moved more than {@code maxMovePercent} against the rate in use (a decimal-point slip
 *       or a wrong base shows up as a move of 90% or 10x);</li>
 *   <li>base/peg is within {@code pegTolerancePercent} of 1.0000. NAD is pegged 1:1 to ZAR by law
 *       (Common Monetary Area), so anything else is a data error, not news.</li>
 * </ul>
 */
@Component
public class RateSanityCheck {

    public enum Reason {
        MISSING_QUOTE, DUPLICATE_QUOTE, NON_POSITIVE, FUTURE_DATE, MOVE_TOO_LARGE, PEG_VIOLATION
    }

    public record Violation(Reason reason, String message) {
    }

    private static final BigDecimal HUNDRED = BigDecimal.valueOf(100);

    private final FxProperties properties;

    public RateSanityCheck(FxProperties properties) {
        this.properties = properties;
    }

    public List<Violation> check(FetchedRates fetched, Optional<RateSnapshot> inUse, LocalDate today) {
        List<Violation> violations = new ArrayList<>();
        Map<String, FetchedRates.Rate> byQuote = new HashMap<>();
        for (FetchedRates.Rate rate : fetched.rates()) {
            if (byQuote.putIfAbsent(rate.quote(), rate) != null) {
                violations.add(new Violation(Reason.DUPLICATE_QUOTE, rate.quote() + " appears more than once"));
            }
        }
        Set<String> expected = properties.quotes();
        for (String quote : expected) {
            if (!byQuote.containsKey(quote)) {
                violations.add(new Violation(Reason.MISSING_QUOTE, quote + " is missing"));
            }
        }

        for (FetchedRates.Rate rate : byQuote.values()) {
            if (rate.rate() == null || rate.rate().signum() <= 0) {
                violations.add(new Violation(Reason.NON_POSITIVE, rate.quote() + " has rate " + rate.rate()));
                continue;
            }
            if (rate.asOf() == null || rate.asOf().isAfter(today.plusDays(1))) {
                violations.add(new Violation(Reason.FUTURE_DATE, rate.quote() + " is dated " + rate.asOf()));
            }
            inUse.flatMap(snapshot -> snapshot.quote(rate.quote())).ifPresent(previous -> {
                BigDecimal movePercent = rate.rate().divide(previous.rate(), MathContext.DECIMAL64)
                        .subtract(BigDecimal.ONE).abs().multiply(HUNDRED);
                if (movePercent.compareTo(properties.maxMovePercent()) > 0) {
                    violations.add(new Violation(Reason.MOVE_TOO_LARGE, "%s moved %s%% (%s to %s), limit %s%%"
                            .formatted(rate.quote(), movePercent.setScale(2, RoundingMode.HALF_EVEN),
                                    previous.rate().stripTrailingZeros().toPlainString(),
                                    rate.rate().stripTrailingZeros().toPlainString(), properties.maxMovePercent())));
                }
            });
        }

        FetchedRates.Rate peg = byQuote.get(properties.pegCurrency());
        if (peg != null && peg.rate() != null && peg.rate().signum() > 0) {
            BigDecimal offPercent = peg.rate().subtract(BigDecimal.ONE).abs().multiply(HUNDRED);
            if (offPercent.compareTo(properties.pegTolerancePercent()) > 0) {
                violations.add(new Violation(Reason.PEG_VIOLATION, "%s/%s is %s, more than %s%% from the 1:1 peg"
                        .formatted(fetched.base(), properties.pegCurrency(), peg.rate().toPlainString(),
                                properties.pegTolerancePercent())));
            }
        }
        return violations;
    }
}
