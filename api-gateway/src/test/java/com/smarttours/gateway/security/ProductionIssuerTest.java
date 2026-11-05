package com.smarttours.gateway.security;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import static com.smarttours.gateway.security.IssuerTestSupport.AUDIENCE;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_SECRET;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_KEY;
import static com.smarttours.gateway.security.IssuerTestSupport.claims;
import static com.smarttours.gateway.security.IssuerTestSupport.jwksEndpoint;
import static com.smarttours.gateway.security.IssuerTestSupport.signHs256;
import static com.smarttours.gateway.security.IssuerTestSupport.signRs256;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.reactive.server.SecurityMockServerConfigurers.springSecurity;

/** The production shape: Keycloak only. A well-signed dev token, even with the right secret present, is a 401. */
@SpringBootTest(properties = {
        "smarttours.security.jwt-secret=" + DEV_SECRET,
        "smarttours.security.keycloak.issuer-uri=" + KEYCLOAK_ISSUER,
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
@ActiveProfiles(TokenIssuers.PRODUCTION_PROFILE)
class ProductionIssuerTest {

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
    void aDevTokenIsRejected() {
        String token = signHs256(claims(DEV_ISSUER, AUDIENCE, "ADMIN"));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().isUnauthorized();
    }

    @Test
    void aKeycloakTokenIsAccepted() {
        String token = signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "CUSTOMER"));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }
}
