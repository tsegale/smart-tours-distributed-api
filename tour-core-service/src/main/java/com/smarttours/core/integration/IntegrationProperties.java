package com.smarttours.core.integration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Outbound providers, one block each (named fields rather than a map, so environment variables such as
 * {@code SMARTTOURS_INTEGRATIONS_OPENMETEOFORECAST_BASEURL} bind to them).
 *
 * @param userAgent       sent on every outbound request, so a provider can tell who is calling and how to reach us
 * @param openMeteoApiKey optional: when set, Open-Meteo is called on its commercial hosts with this key (the free
 *                        API is for non-commercial use only, ADR 0012); blank uses the free API
 */
@ConfigurationProperties("smarttours.integrations")
public record IntegrationProperties(
        String userAgent,
        ProviderProperties frankfurter,
        ProviderProperties openMeteoForecast,
        ProviderProperties openMeteoArchive,
        String openMeteoApiKey) {

    public boolean hasOpenMeteoApiKey() {
        return openMeteoApiKey != null && !openMeteoApiKey.isBlank();
    }
}
