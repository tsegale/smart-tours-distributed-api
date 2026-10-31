package com.smarttours.core.weather;

import com.smarttours.core.availability.DepartureSummaryRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * Weather embedded in tour detail ({@code include=weather}): for the date asked for, else the tour's next
 * departure with seats left, waiting only the short include budget so the page stays fast.
 */
@Service
public class DepartureWeather {

    private final WeatherService weather;
    private final DepartureSummaryRepository departures;
    private final WeatherProperties properties;

    public DepartureWeather(WeatherService weather, DepartureSummaryRepository departures, WeatherProperties properties) {
        this.weather = weather;
        this.departures = departures;
        this.properties = properties;
    }

    public WeatherLookup forTour(long tourId, double latitude, double longitude, LocalDate requestedDate) {
        Optional<LocalDate> date = requestedDate != null
                ? Optional.of(requestedDate)
                : departures.nextDepartureDate(tourId, properties.zone());
        return date.map(day -> weather.lookup(latitude, longitude, day, properties.includeBudget()))
                .orElseGet(WeatherLookup::noDeparture);
    }
}
