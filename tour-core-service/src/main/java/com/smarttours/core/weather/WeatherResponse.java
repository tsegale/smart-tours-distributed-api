package com.smarttours.core.weather;

import java.time.LocalDate;

/** {@code GET /api/tours/{id}/weather}: always 200 for a valid request; {@code weatherStatus} tells what came back. */
public record WeatherResponse(long tourId, LocalDate date, WeatherReport weather, WeatherStatus weatherStatus,
                              WeatherAttribution attribution) {
}
