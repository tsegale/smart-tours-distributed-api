package com.smarttours.core.integration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Outbound providers, one block each (named fields rather than a map, so environment variables such as
 * {@code SMARTTOURS_INTEGRATIONS_OPENMETEOFORECAST_BASEURL} bind to them).
 *
 * @param userAgent sent on every outbound request, so a provider can tell who is calling and how to reach us
 */
@ConfigurationProperties("smarttours.integrations")
public record IntegrationProperties(
        String userAgent,
        ProviderProperties frankfurter,
        ProviderProperties openMeteoForecast,
        ProviderProperties openMeteoArchive) {
}
