package com.smarttours.core.fx.frankfurter;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * One row of {@code /v2/rates}, e.g. {@code {"date":"2026-10-31","base":"NAD","quote":"EUR","rate":0.053827}}
 * (recorded in {@code src/test/resources/fixtures/frankfurter}). Other fields, such as the per-provider
 * breakdown that {@code expand=providers} adds, are ignored.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record FrankfurterRate(LocalDate date, String base, String quote, BigDecimal rate) {
}
