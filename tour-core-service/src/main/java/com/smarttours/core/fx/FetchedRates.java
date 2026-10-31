package com.smarttours.core.fx;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * What a provider returned, before any sanity check.
 *
 * @param source who calculated the rates, stored with each row for audits (e.g. {@code frankfurter:blended})
 */
public record FetchedRates(String base, String source, List<Rate> rates) {

    /** 1 base = {@code rate} quote, as published for {@code asOf}. */
    public record Rate(String quote, BigDecimal rate, LocalDate asOf) {
    }
}
