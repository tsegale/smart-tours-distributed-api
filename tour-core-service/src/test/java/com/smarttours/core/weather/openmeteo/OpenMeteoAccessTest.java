package com.smarttours.core.weather.openmeteo;

import com.smarttours.core.integration.OutboundClients;
import org.junit.jupiter.api.Test;

import java.net.URI;

import static org.assertj.core.api.Assertions.assertThat;

class OpenMeteoAccessTest {

    private static final OutboundClients.QueryKey KEY = new OutboundClients.QueryKey("apikey", "k");

    @Test
    void aKeyMovesTheFreeHostsToTheCommercialOnes() {
        assertThat(OpenMeteoAccess.baseUrl(URI.create("https://api.open-meteo.com"), KEY))
                .isEqualTo(URI.create("https://customer-api.open-meteo.com"));
        assertThat(OpenMeteoAccess.baseUrl(URI.create("https://archive-api.open-meteo.com"), KEY))
                .isEqualTo(URI.create("https://customer-archive-api.open-meteo.com"));
    }

    @Test
    void withoutAKeyOrOnAnotherHostTheBaseUrlIsKept() {
        assertThat(OpenMeteoAccess.baseUrl(URI.create("https://api.open-meteo.com"), null))
                .isEqualTo(URI.create("https://api.open-meteo.com"));
        assertThat(OpenMeteoAccess.baseUrl(URI.create("http://provider-stubs:8080"), KEY))
                .isEqualTo(URI.create("http://provider-stubs:8080"));
    }

    @Test
    void theKeyNeverShowsInItsStringForm() {
        assertThat(new OutboundClients.QueryKey("apikey", "secret-value").toString()).doesNotContain("secret-value");
    }
}
