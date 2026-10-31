package com.smarttours.core.weather.openmeteo;

import com.smarttours.core.integration.IntegrationProperties;
import com.smarttours.core.integration.OutboundClients;
import com.smarttours.core.integration.ProviderPolicies;
import com.smarttours.core.integration.ProviderPolicy;
import com.smarttours.core.weather.DailyWeather;
import com.smarttours.core.weather.GeoCell;
import com.smarttours.core.weather.WeatherProvider;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/**
 * Outbound adapter for Open-Meteo. Forecast and archive are separate hosts with separate policies
 * ({@value #FORECAST}, {@value #ARCHIVE}): an archive outage must not open the forecast's circuit.
 * Metric units are the API's defaults (°C, km/h, mm); {@code timezone=auto} gives dates and sunrise/sunset
 * in the location's local time.
 */
@Component
public class OpenMeteoWeatherProvider implements WeatherProvider {

    public static final String FORECAST = "open-meteo-forecast";
    public static final String ARCHIVE = "open-meteo-archive";

    static final String FORECAST_DAILY = "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
            + "precipitation_sum,wind_speed_10m_max,weather_code,sunrise,sunset";
    static final String ARCHIVE_DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,"
            + "wind_speed_10m_max,weather_code,sunrise,sunset";

    private final OpenMeteoForecastClient forecastClient;
    private final OpenMeteoArchiveClient archiveClient;
    private final ProviderPolicy forecastPolicy;
    private final ProviderPolicy archivePolicy;

    public OpenMeteoWeatherProvider(OutboundClients clients, ProviderPolicies policies, IntegrationProperties properties) {
        this.forecastClient = clients.create(OpenMeteoForecastClient.class, FORECAST, properties.openMeteoForecast());
        this.archiveClient = clients.create(OpenMeteoArchiveClient.class, ARCHIVE, properties.openMeteoArchive());
        this.forecastPolicy = policies.create(FORECAST, properties.openMeteoForecast());
        this.archivePolicy = policies.create(ARCHIVE, properties.openMeteoArchive());
    }

    @Override
    public CompletableFuture<DailyWeather> forecast(GeoCell cell, LocalDate date) {
        // Mapped inside the policy: an unusable 200 is a provider failure and counts against its circuit.
        return forecastPolicy.call(() -> days(forecastClient.forecast(cell.latitude().toPlainString(),
                        cell.longitude().toPlainString(), FORECAST_DAILY, "auto", date.toString(), date.toString()))
                .stream()
                .filter(day -> date.equals(day.date()))
                .findFirst()
                .orElseThrow(() -> new MalformedWeatherResponseException("no entry for " + date)));
    }

    @Override
    public CompletableFuture<List<DailyWeather>> history(GeoCell cell, LocalDate from, LocalDate to) {
        return archivePolicy.call(() -> days(archiveClient.archive(cell.latitude().toPlainString(),
                cell.longitude().toPlainString(), ARCHIVE_DAILY, "auto", from.toString(), to.toString())));
    }

    static List<DailyWeather> days(OpenMeteoDailyResponse response) {
        if (response == null || response.daily() == null || response.daily().time() == null) {
            throw new MalformedWeatherResponseException("no daily block");
        }
        OpenMeteoDailyResponse.Daily daily = response.daily();
        List<DailyWeather> days = new ArrayList<>(daily.time().size());
        for (int i = 0; i < daily.time().size(); i++) {
            days.add(new DailyWeather(
                    daily.time().get(i),
                    response.timezone(),
                    at(daily.temperatureMax(), i),
                    at(daily.temperatureMin(), i),
                    at(daily.precipitationProbabilityMax(), i),
                    at(daily.precipitationSum(), i),
                    at(daily.windSpeedMax(), i),
                    at(daily.weatherCode(), i),
                    timeOf(at(daily.sunrise(), i)),
                    timeOf(at(daily.sunset(), i))));
        }
        return days;
    }

    /** A missing variable, or a shorter array than {@code time}, is missing data for that day, not an error. */
    private static <T> T at(List<T> values, int index) {
        return values == null || index >= values.size() ? null : values.get(index);
    }

    private static LocalTime timeOf(LocalDateTime dateTime) {
        return dateTime == null ? null : dateTime.toLocalTime();
    }

    /** The provider answered 200 with a body we can't use: counts against it, isn't retried. */
    public static class MalformedWeatherResponseException extends RuntimeException {
        public MalformedWeatherResponseException(String detail) {
            super("Unusable Open-Meteo response: " + detail);
        }
    }

    ProviderPolicy forecastPolicy() {
        return forecastPolicy;
    }

    ProviderPolicy archivePolicy() {
        return archivePolicy;
    }
}
