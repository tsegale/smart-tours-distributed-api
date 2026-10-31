package com.smarttours.core.weather;

/**
 * Open-Meteo's data is CC BY 4.0, and its licence asks for a link next to wherever the data is shown.
 * Sent with every weather answer so a client can't show the data without it.
 */
public record WeatherAttribution(String text, String url, String licence, String licenceUrl) {

    public static final WeatherAttribution OPEN_METEO = new WeatherAttribution(
            "Weather data by Open-Meteo.com", "https://open-meteo.com/",
            "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/");
}
