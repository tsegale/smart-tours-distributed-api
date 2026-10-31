package com.smarttours.core.fx;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.Currency;

/**
 * The conversion one request asked for, decided once per request and applied to every price in the
 * response (after the cache, so cached entries stay currency-independent: ADR 0011).
 *
 * <p>Amounts are rounded HALF_EVEN (banker's rounding: no upward bias over many prices) to the target
 * currency's ISO 4217 minor units, from {@link Currency#getDefaultFractionDigits()}: 2 for EUR, 0 for JPY.
 */
public final class CurrencyConversion {

    private enum Kind { NONE, IDENTITY, RATE, UNAVAILABLE }

    private final Kind kind;
    private final Currency target;
    private final BigDecimal rate;
    private final LocalDate rateAsOf;
    private final boolean stale;
    private final String unavailableReason;

    private CurrencyConversion(Kind kind, Currency target, BigDecimal rate, LocalDate rateAsOf, boolean stale,
                               String unavailableReason) {
        this.kind = kind;
        this.target = target;
        this.rate = rate;
        this.rateAsOf = rateAsOf;
        this.stale = stale;
        this.unavailableReason = unavailableReason;
    }

    /** No {@code ?currency=}: responses carry the base price only. */
    public static CurrencyConversion none() {
        return new CurrencyConversion(Kind.NONE, null, null, null, false, null);
    }

    /** The base currency was asked for: displayPrice is the price itself, at rate 1. */
    public static CurrencyConversion identity(Currency base) {
        return new CurrencyConversion(Kind.IDENTITY, base, BigDecimal.ONE, null, false, null);
    }

    public static CurrencyConversion at(Currency target, BigDecimal rate, LocalDate rateAsOf, boolean stale) {
        return new CurrencyConversion(Kind.RATE, target, rate, rateAsOf, stale, null);
    }

    /** Asked for, but no usable rate: displayPrice is omitted and the reason given instead. */
    public static CurrencyConversion unavailable(Currency target, String reason) {
        return new CurrencyConversion(Kind.UNAVAILABLE, target, null, null, false, reason);
    }

    /** True when the request named a currency (the base included): the response then differs from the cached one. */
    public boolean changesRepresentation() {
        return kind != Kind.NONE;
    }

    /** The display price of a base amount, or null when none was asked for or none can be given. */
    public DisplayPrice display(BigDecimal baseAmount) {
        return switch (kind) {
            case NONE, UNAVAILABLE -> null;
            case IDENTITY -> new DisplayPrice(baseAmount.setScale(target.getDefaultFractionDigits(), RoundingMode.HALF_EVEN),
                    target.getCurrencyCode(), BigDecimal.ONE, null, false);
            case RATE -> new DisplayPrice(convert(baseAmount, rate, target), target.getCurrencyCode(),
                    rate.stripTrailingZeros(), rateAsOf, stale);
        };
    }

    /** Why there is no display price; null when there is one or none was asked for. */
    public String unavailableReason() {
        return unavailableReason;
    }

    /**
     * An amount given in the display currency (a search's price bound), in the base currency, to the
     * cent. {@code rounding} widens the bound so a tour displayed at exactly the bound is still included:
     * FLOOR for a minimum, CEILING for a maximum.
     */
    public BigDecimal toBase(BigDecimal displayAmount, RoundingMode rounding) {
        return switch (kind) {
            case NONE, IDENTITY -> displayAmount;
            case RATE -> displayAmount.divide(rate, MathContext.DECIMAL64).setScale(2, rounding);
            case UNAVAILABLE -> throw new ExchangeRatesUnavailableException(target.getCurrencyCode(), unavailableReason);
        };
    }

    static BigDecimal convert(BigDecimal baseAmount, BigDecimal rate, Currency target) {
        return baseAmount.multiply(rate).setScale(target.getDefaultFractionDigits(), RoundingMode.HALF_EVEN);
    }
}
