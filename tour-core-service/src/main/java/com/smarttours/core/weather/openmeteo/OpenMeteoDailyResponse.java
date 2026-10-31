package com.smarttours.core.weather.openmeteo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

/**
 * The {@code daily} block of an Open-Meteo forecast or archive response: parallel arrays, one entry per day
 * (fixtures in {@code src/test/resources/fixtures/open-meteo}). The archive has no precipitation probability.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record OpenMeteoDailyResponse(double latitude, double longitude, String timezone, Daily daily) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Daily(
            List<LocalDate> time,
            @JsonProperty("temperature_2m_max") List<Double> temperatureMax,
            @JsonProperty("temperature_2m_min") List<Double> temperatureMin,
            @JsonProperty("precipitation_probability_max") List<Integer> precipitationProbabilityMax,
            @JsonProperty("precipitation_sum") List<Double> precipitationSum,
            @JsonProperty("wind_speed_10m_max") List<Double> windSpeedMax,
            @JsonProperty("weather_code") List<Integer> weatherCode,
            List<LocalDateTime> sunrise,
            List<LocalDateTime> sunset) {
    }
}
