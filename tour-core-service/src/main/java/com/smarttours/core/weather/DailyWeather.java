package com.smarttours.core.weather;

import java.time.LocalDate;
import java.time.LocalTime;

/**
 * One day of weather at a place, in local time, metric units, as a provider reported it. Any value may be
 * null when the provider has no data for it (e.g. precipitation probability does not exist in the archive).
 */
public record DailyWeather(
        LocalDate date,
        String timezone,
        Double temperatureMaxC,
        Double temperatureMinC,
        Integer precipitationProbabilityPercent,
        Double precipitationSumMm,
        Double windSpeedMaxKmh,
        Integer weatherCode,
        LocalTime sunrise,
        LocalTime sunset) {
}
