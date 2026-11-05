package com.smarttours.gateway.security;

import org.junit.jupiter.api.Test;

import java.util.Base64;

import static com.smarttours.gateway.security.IssuerTestSupport.AUDIENCE;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_SECRET;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_ISSUER;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** What stops a misconfigured gateway from starting. */
class TokenIssuersTest {

    private static final SecurityProperties.Keycloak NO_KEYCLOAK = new SecurityProperties.Keycloak("", "");
    private static final SecurityProperties.Keycloak KEYCLOAK = new SecurityProperties.Keycloak(KEYCLOAK_ISSUER, "");
    private static final SecurityProperties.DevTokens DEV_ON = new SecurityProperties.DevTokens(true, DEV_ISSUER);
    private static final SecurityProperties.DevTokens DEV_OFF = new SecurityProperties.DevTokens(false, DEV_ISSUER);

    @Test
    void productionRefusesToStartWithDevTokensOn() {
        var properties = new SecurityProperties(DEV_SECRET, AUDIENCE, DEV_ON, KEYCLOAK);
        assertThatThrownBy(() -> TokenIssuers.managers(properties, true, SecurityConfig.jwtAuthenticationConverter()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("production");
    }

    @Test
    void refusesToStartWithNoIssuerAtAll() {
        var properties = new SecurityProperties("", AUDIENCE, DEV_OFF, NO_KEYCLOAK);
        assertThatThrownBy(() -> TokenIssuers.managers(properties, false, SecurityConfig.jwtAuthenticationConverter()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("No token issuer");
    }

    @Test
    void refusesToStartWithoutAnAudience() {
        var properties = new SecurityProperties(DEV_SECRET, " ", DEV_ON, KEYCLOAK);
        assertThatThrownBy(() -> TokenIssuers.managers(properties, false, SecurityConfig.jwtAuthenticationConverter()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("audience");
    }

    @Test
    void devTokensNeedASecret() {
        var properties = new SecurityProperties("", AUDIENCE, DEV_ON, NO_KEYCLOAK);
        assertThatThrownBy(() -> TokenIssuers.managers(properties, false, SecurityConfig.jwtAuthenticationConverter()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("JWT_SECRET");
    }

    @Test
    void devTokensNeedALongEnoughSecret() {
        String tooShort = Base64.getEncoder().encodeToString(new byte[16]);
        var properties = new SecurityProperties(tooShort, AUDIENCE, DEV_ON, NO_KEYCLOAK);
        assertThatThrownBy(() -> TokenIssuers.managers(properties, false, SecurityConfig.jwtAuthenticationConverter()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("at least 32 bytes");
    }

    @Test
    void theDevSecretIsNotNeededWithoutDevTokens() {
        var properties = new SecurityProperties("", AUDIENCE, DEV_OFF, KEYCLOAK);
        assertThat(TokenIssuers.managers(properties, true, SecurityConfig.jwtAuthenticationConverter()))
                .containsOnlyKeys(KEYCLOAK_ISSUER);
    }

    @Test
    void bothIssuersCanBeTrustedOutsideProduction() {
        var properties = new SecurityProperties(DEV_SECRET, AUDIENCE, DEV_ON, KEYCLOAK);
        assertThat(TokenIssuers.managers(properties, false, SecurityConfig.jwtAuthenticationConverter()))
                .containsOnlyKeys(DEV_ISSUER, KEYCLOAK_ISSUER);
    }
}
