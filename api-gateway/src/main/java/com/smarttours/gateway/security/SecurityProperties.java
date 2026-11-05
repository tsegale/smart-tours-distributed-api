package com.smarttours.gateway.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Who the gateway accepts tokens from (ADR 0016).
 *
 * @param jwtSecret base64 HS256 key, used only when dev tokens are enabled
 * @param audience  the value every token's {@code aud} must contain, whichever issuer signed it
 * @param devTokens the local HS256 issuer behind scripts/dev_token.py
 * @param keycloak  the real issuer; blank issuer URI means none is configured
 */
@ConfigurationProperties("smarttours.security")
public record SecurityProperties(String jwtSecret, String audience, DevTokens devTokens, Keycloak keycloak) {

    public record DevTokens(boolean enabled, String issuer) {
    }

    /**
     * @param issuerUri the exact {@code iss} in Keycloak's tokens: the URL clients use, not the internal one
     * @param jwkSetUri where the gateway fetches signing keys; blank derives it from the issuer URI
     */
    public record Keycloak(String issuerUri, String jwkSetUri) {
    }
}
