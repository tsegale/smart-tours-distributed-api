package com.smarttours.core.weather;

import com.smarttours.core.tour.TourService;
import com.smarttours.core.tour.dto.TourResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

@RestController
public class WeatherController {

    private final TourService tours;
    private final WeatherService weather;
    private final WeatherProperties properties;

    public WeatherController(TourService tours, WeatherService weather, WeatherProperties properties) {
        this.tours = tours;
        this.weather = weather;
        this.properties = properties;
    }

    @Operation(summary = "Weather for a departure",
            description = "Forecast for dates up to 15 days ahead, else the climate normal (the same calendar week "
                    + "averaged over the last 10 years). Always 200 for a valid request: when the provider is down, "
                    + "the last known value with stale=true, or weather=null with weatherStatus=unavailable.")
    @ApiResponse(responseCode = "200", description = "Weather, or the reason there is none (weatherStatus)")
    @ApiResponse(responseCode = "400", description = "Missing, malformed or past date (Problem Details)")
    @ApiResponse(responseCode = "404", description = "No such tour (Problem Details)")
    @GetMapping("/api/tours/{id}/weather")
    public WeatherResponse weather(@PathVariable long id,
                                   @Parameter(description = "Departure date, YYYY-MM-DD, today or later (Africa/Windhoek)")
                                   @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date) {
        TourResponse tour = tours.get(id).tour();
        WeatherLookup lookup = weather.lookup(tour.latitude(), tour.longitude(), date, properties.requestBudget());
        return new WeatherResponse(id, date, lookup.weather(), lookup.status(), WeatherAttribution.OPEN_METEO);
    }
}
