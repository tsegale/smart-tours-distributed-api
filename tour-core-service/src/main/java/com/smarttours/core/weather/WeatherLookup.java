package com.smarttours.core.weather;

/** The outcome of one weather lookup: never an exception, always something a response can carry. */
public record WeatherLookup(WeatherReport weather, WeatherStatus status) {

    public static WeatherLookup ok(WeatherReport weather) {
        return new WeatherLookup(weather, WeatherStatus.OK);
    }

    public static WeatherLookup stale(WeatherReport weather) {
        return new WeatherLookup(weather.asStale(), WeatherStatus.STALE);
    }

    public static WeatherLookup unavailable() {
        return new WeatherLookup(null, WeatherStatus.UNAVAILABLE);
    }

    public static WeatherLookup noDeparture() {
        return new WeatherLookup(null, WeatherStatus.NO_DEPARTURE);
    }
}
