package com.smarttours.core.weather.openmeteo;

import com.smarttours.core.integration.IntegrationProperties;
import com.smarttours.core.integration.OutboundClients;

import java.net.URI;
import java.util.Set;

/**
 * Free or commercial Open-Meteo. The commercial API has the same paths and parameters on hosts prefixed with
 * {@code customer-}, and takes the key as the {@code apikey} query parameter. A base URL that isn't one of
 * Open-Meteo's free hosts (WireMock in tests, the chaos stubs) is kept as it is, with the key still sent.
 */
final class OpenMeteoAccess {

    static final String KEY_PARAMETER = "apikey";
    static final String COMMERCIAL_PREFIX = "customer-";
    static final Set<String> FREE_HOSTS = Set.of("api.open-meteo.com", "archive-api.open-meteo.com");

    private OpenMeteoAccess() {
    }

    static OutboundClients.QueryKey key(IntegrationProperties properties) {
        return properties.hasOpenMeteoApiKey()
                ? new OutboundClients.QueryKey(KEY_PARAMETER, properties.openMeteoApiKey().strip())
                : null;
    }

    static URI baseUrl(URI configured, OutboundClients.QueryKey key) {
        if (key == null || !FREE_HOSTS.contains(configured.getHost())) {
            return configured;
        }
        return URI.create(configured.toString().replaceFirst("//" + configured.getHost(),
                "//" + COMMERCIAL_PREFIX + configured.getHost()));
    }
}
