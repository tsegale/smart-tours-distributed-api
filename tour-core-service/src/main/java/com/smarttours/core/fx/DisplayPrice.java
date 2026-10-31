package com.smarttours.core.fx;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * A price converted for display. Never charged: bookings are charged in the base currency (ADR 0011).
 *
 * @param rate     1 base = rate currency, as used for this amount
 * @param rateAsOf publication date of that rate; null for the base currency itself
 * @param stale    the rate is older than the fresh window but still within the maximum age
 */
public record DisplayPrice(BigDecimal amount, String currency, BigDecimal rate, LocalDate rateAsOf, boolean stale) {
}
