package com.smarttours.gateway.security;

import io.netty.channel.ChannelOption;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.convert.converter.Converter;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.security.authentication.ReactiveAuthenticationManager;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimNames;
import org.springframework.security.oauth2.jwt.JwtClaimValidator;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusReactiveJwtDecoder;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtReactiveAuthenticationManager;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

import javax.crypto.spec.SecretKeySpec;
import java.time.Duration;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * One authentication manager per trusted issuer, each with its own key source, algorithm and validators. An HS256
 * token can never be checked against Keycloak's keys or the other way round: the unverified {@code iss} only picks
 * the manager, and that manager then requires its own algorithm, its own key and the same {@code iss} again.
 *
 * <p>The checks here fail at startup, so a misconfigured gateway never serves traffic: production must not trust
 * the dev issuer, and a gateway that trusts nobody would answer 401 to every signed-in caller.
 */
final class TokenIssuers {

    static final int MIN_SECRET_BYTES = 32; // HS256 needs a key of at least 256 bits
    static final String PRODUCTION_PROFILE = "prod";
    private static final Duration JWKS_TIMEOUT = Duration.ofSeconds(5);
    private static final Logger log = LoggerFactory.getLogger(TokenIssuers.class);

    private TokenIssuers() {
    }

    static Map<String, ReactiveAuthenticationManager> managers(
            SecurityProperties properties,
            boolean production,
            Converter<Jwt, Mono<AbstractAuthenticationToken>> converter) {
        if (production && properties.devTokens().enabled()) {
            throw new IllegalStateException("Dev tokens must stay disabled in production: set DEV_TOKENS_ENABLED=false");
        }
        if (isBlank(properties.audience())) {
            throw new IllegalStateException("smarttours.security.audience must be set");
        }

        Map<String, ReactiveAuthenticationManager> managers = new HashMap<>();
        if (properties.devTokens().enabled()) {
            log.warn("Accepting HS256 dev tokens from issuer {}. Never do this in production.",
                    properties.devTokens().issuer());
            managers.put(properties.devTokens().issuer(), devManager(properties, converter));
        }
        String keycloakIssuer = properties.keycloak().issuerUri();
        if (!isBlank(keycloakIssuer)) {
            managers.put(keycloakIssuer, keycloakManager(properties, converter));
        }
        if (managers.isEmpty()) {
            throw new IllegalStateException("No token issuer configured: set KEYCLOAK_ISSUER_URI or DEV_TOKENS_ENABLED=true");
        }
        return Map.copyOf(managers);
    }

    private static ReactiveAuthenticationManager devManager(
            SecurityProperties properties, Converter<Jwt, Mono<AbstractAuthenticationToken>> converter) {
        if (isBlank(properties.jwtSecret())) {
            throw new IllegalStateException("Dev tokens are enabled but JWT_SECRET is not set");
        }
        byte[] key = Base64.getDecoder().decode(properties.jwtSecret());
        if (key.length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "JWT_SECRET must decode to at least " + MIN_SECRET_BYTES + " bytes (openssl rand -base64 48)");
        }
        NimbusReactiveJwtDecoder decoder = NimbusReactiveJwtDecoder
                .withSecretKey(new SecretKeySpec(key, "HmacSHA256"))
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
        decoder.setJwtValidator(validator(properties.devTokens().issuer(), properties.audience()));
        return manager(decoder, converter);
    }

    private static ReactiveAuthenticationManager keycloakManager(
            SecurityProperties properties, Converter<Jwt, Mono<AbstractAuthenticationToken>> converter) {
        String issuer = properties.keycloak().issuerUri();
        String jwkSetUri = isBlank(properties.keycloak().jwkSetUri())
                ? issuer + "/protocol/openid-connect/certs"
                : properties.keycloak().jwkSetUri();
        // Keys are fetched on first use, so the gateway starts and serves dev tokens with Keycloak down.
        NimbusReactiveJwtDecoder decoder = NimbusReactiveJwtDecoder
                .withJwkSetUri(jwkSetUri)
                .jwsAlgorithm(SignatureAlgorithm.RS256)
                .webClient(jwksClient())
                .build();
        decoder.setJwtValidator(validator(issuer, properties.audience()));
        return manager(reportingKeyOutages(decoder), converter);
    }

    /**
     * A failed key fetch surfaces as an IllegalStateException, not a JwtException, and would answer 500. Bad tokens stay
     * JwtExceptions (401); anything else means the token couldn't be checked ({@link IssuerOutageExceptionHandler}).
     */
    private static ReactiveJwtDecoder reportingKeyOutages(ReactiveJwtDecoder decoder) {
        return token -> decoder.decode(token).onErrorMap(
                failure -> !(failure instanceof JwtException),
                failure -> new AuthenticationServiceException("Could not obtain the issuer's keys", failure));
    }

    /** A hung key endpoint must not hold requests open: signed-in requests wait on this during a key fetch. */
    private static WebClient jwksClient() {
        HttpClient http = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, (int) JWKS_TIMEOUT.toMillis())
                .responseTimeout(JWKS_TIMEOUT);
        return WebClient.builder().clientConnector(new ReactorClientHttpConnector(http)).build();
    }

    private static OAuth2TokenValidator<Jwt> validator(String issuer, String audience) {
        return new DelegatingOAuth2TokenValidator<>(
                JwtValidators.createDefaultWithIssuer(issuer),
                new JwtClaimValidator<List<String>>(JwtClaimNames.AUD, aud -> aud != null && aud.contains(audience)));
    }

    private static ReactiveAuthenticationManager manager(
            ReactiveJwtDecoder decoder, Converter<Jwt, Mono<AbstractAuthenticationToken>> converter) {
        JwtReactiveAuthenticationManager manager = new JwtReactiveAuthenticationManager(decoder);
        manager.setJwtAuthenticationConverter(converter);
        return manager;
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
