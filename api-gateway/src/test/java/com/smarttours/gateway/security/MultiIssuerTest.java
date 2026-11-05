package com.smarttours.gateway.security;

import com.nimbusds.jose.jwk.RSAKey;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;

import static com.smarttours.gateway.security.IssuerTestSupport.AUDIENCE;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_SECRET;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_KEY;
import static com.smarttours.gateway.security.IssuerTestSupport.claims;
import static com.smarttours.gateway.security.IssuerTestSupport.jwksEndpoint;
import static com.smarttours.gateway.security.IssuerTestSupport.rsaKey;
import static com.smarttours.gateway.security.IssuerTestSupport.signHs256;
import static com.smarttours.gateway.security.IssuerTestSupport.signRs256;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.reactive.server.SecurityMockServerConfigurers.springSecurity;

/** Both issuers on, as in local development. Downstream isn't running: passing security ends in a 5xx. */
@SpringBootTest(properties = {
        "smarttours.security.dev-tokens.enabled=true",
        "smarttours.security.jwt-secret=" + DEV_SECRET,
        "smarttours.security.keycloak.issuer-uri=" + KEYCLOAK_ISSUER,
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
class MultiIssuerTest {

    private static final HttpServer JWKS = jwksEndpoint(KEYCLOAK_KEY);

    @Autowired
    private ApplicationContext context;

    private WebTestClient client;

    @DynamicPropertySource
    static void keycloakKeys(DynamicPropertyRegistry registry) {
        registry.add("smarttours.security.keycloak.jwk-set-uri",
                () -> "http://localhost:" + JWKS.getAddress().getPort() + "/certs");
    }

    @AfterAll
    static void stopJwks() {
        JWKS.stop(0);
    }

    @BeforeEach
    void setUp() {
        client = WebTestClient.bindToApplicationContext(context).apply(springSecurity()).configureClient().build();
    }

    @Test
    void aKeycloakTokenIsAccepted() {
        assertPassesSecurity(signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "CUSTOMER")));
    }

    @Test
    void aDevTokenIsAccepted() {
        assertPassesSecurity(signHs256(claims(DEV_ISSUER, AUDIENCE, "CUSTOMER")));
    }

    @Test
    void rolesFromAKeycloakTokenDecideAccess() {
        String customer = signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "CUSTOMER"));
        client.post().uri("/api/tours").header("Authorization", "Bearer " + customer)
                .exchange().expectStatus().isForbidden();

        String operator = signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "OPERATOR"));
        client.post().uri("/api/tours").header("Authorization", "Bearer " + operator)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void anUntrustedIssuerIsRejected_evenWithAValidSignature() {
        assertRejected(signRs256(KEYCLOAK_KEY, claims("https://evil.example.test/realms/smart-tours", AUDIENCE, "ADMIN")));
        assertRejected(signHs256(claims("https://evil.example.test", AUDIENCE, "ADMIN")));
    }

    @Test
    void aTokenWithNoIssuerIsRejected() {
        assertRejected(signHs256(claims(null, AUDIENCE, "ADMIN")));
    }

    @Test
    void theWrongAudienceIsRejected_fromEitherIssuer() {
        assertRejected(signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, "another-api", "CUSTOMER")));
        assertRejected(signHs256(claims(DEV_ISSUER, "another-api", "CUSTOMER")));
    }

    @Test
    void aMissingAudienceIsRejected_fromEitherIssuer() {
        assertRejected(signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, null, "CUSTOMER")));
        assertRejected(signHs256(claims(DEV_ISSUER, null, "CUSTOMER")));
    }

    @Test
    void aKeycloakTokenSignedByAnotherKeyIsRejected() {
        RSAKey attacker = rsaKey(KEYCLOAK_KEY.getKeyID());
        assertRejected(signRs256(attacker, claims(KEYCLOAK_ISSUER, AUDIENCE, "ADMIN")));
    }

    @Test
    void theDevSecretCannotSignForKeycloak() {
        assertRejected(signHs256(claims(KEYCLOAK_ISSUER, AUDIENCE, "ADMIN")));
    }

    /** The classic confusion attack: HS256, with the issuer's public key as the shared secret. */
    @Test
    void keycloaksPublicKeyCannotBeUsedAsAnHmacSecret() throws Exception {
        byte[] publicKey = KEYCLOAK_KEY.toPublicJWK().toJSONString().getBytes(StandardCharsets.UTF_8);
        assertRejected(signHs256(publicKey, claims(KEYCLOAK_ISSUER, AUDIENCE, "ADMIN")));
    }

    @Test
    void aKeycloakKeyCannotSignForTheDevIssuer() {
        assertRejected(signRs256(KEYCLOAK_KEY, claims(DEV_ISSUER, AUDIENCE, "ADMIN")));
    }

    @Test
    void anExpiredKeycloakTokenIsRejected() {
        assertRejected(signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "CUSTOMER")
                .expirationTime(Date.from(Instant.now().minusSeconds(3600)))));
    }

    private void assertPassesSecurity(String token) {
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    private void assertRejected(String token) {
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().isUnauthorized();
    }
}
