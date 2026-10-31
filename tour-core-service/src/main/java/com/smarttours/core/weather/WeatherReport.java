package com.smarttours.core.weather;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonValue;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.util.Locale;

/**
 * Weather for one day at a tour's location, as clients see it. Also the value cached in Redis.
 *
 * @param type      {@code forecast} within the provider's horizon, else {@code climate_normal}
 * @param latitude  of the 0.1° cell the weather is for, not the tour's exact point
 * @param precipitationProbabilityPercent forecast: the provider's maximum for the day. Climate normal: the
 *                  share of the sampled days with at least 1 mm of rain
 * @param years     climate normals only: the years averaged
 * @param stale     the provider could not be reached, so this is the last value we had
 */
public record WeatherReport(
        Type type,
        LocalDate date,
        double latitude,
        double longitude,
        String timezone,
        Double temperatureMaxC,
        Double temperatureMinC,
        Integer precipitationProbabilityPercent,
        Double precipitationSumMm,
        Double windSpeedMaxKmh,
        Integer weatherCode,
        String description,
        LocalTime sunrise,
        LocalTime sunset,
        @JsonInclude(JsonInclude.Include.NON_NULL) YearRange years,
        Instant fetchedAt,
        boolean stale) {

    public enum Type {
        FORECAST, CLIMATE_NORMAL;

        @JsonValue
        public String json() {
            return name().toLowerCase(Locale.ROOT);
        }
    }

    /** The sampled years, inclusive; {@code count} is less than to - from + 1 when some years failed to load. */
    public record YearRange(int from, int to, int count) {
    }

    public WeatherReport asStale() {
        return new WeatherReport(type, date, latitude, longitude, timezone, temperatureMaxC, temperatureMinC,
                precipitationProbabilityPercent, precipitationSumMm, windSpeedMaxKmh, weatherCode, description,
                sunrise, sunset, years, fetchedAt, true);
    }

    static WeatherReport forecast(GeoCell cell, DailyWeather day, Instant fetchedAt) {
        return new WeatherReport(Type.FORECAST, day.date(), cell.latitude().doubleValue(),
                cell.longitude().doubleValue(), day.timezone(), day.temperatureMaxC(), day.temperatureMinC(),
                day.precipitationProbabilityPercent(), day.precipitationSumMm(), day.windSpeedMaxKmh(),
                day.weatherCode(), WeatherCodes.describe(day.weatherCode()), day.sunrise(), day.sunset(), null,
                fetchedAt, false);
    }
}
