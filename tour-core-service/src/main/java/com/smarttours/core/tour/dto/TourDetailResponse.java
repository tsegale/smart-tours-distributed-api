package com.smarttours.core.tour.dto;

import com.fasterxml.jackson.annotation.JsonUnwrapped;
import com.smarttours.core.weather.WeatherAttribution;
import com.smarttours.core.weather.WeatherLookup;
import com.smarttours.core.weather.WeatherReport;
import com.smarttours.core.weather.WeatherStatus;

/**
 * Tour detail with {@code include=weather}: the tour's own fields, then {@code weather} (null when there is
 * none), {@code weatherStatus} saying why, and the attribution Open-Meteo's licence requires.
 */
public record TourDetailResponse(
        @JsonUnwrapped TourResponse tour,
        WeatherReport weather,
        WeatherStatus weatherStatus,
        WeatherAttribution weatherAttribution) {

    public static TourDetailResponse of(TourResponse tour, WeatherLookup lookup) {
        return new TourDetailResponse(tour, lookup.weather(), lookup.status(), WeatherAttribution.OPEN_METEO);
    }
}
