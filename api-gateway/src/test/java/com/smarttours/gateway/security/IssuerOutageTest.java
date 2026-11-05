package com.smarttours.gateway.security;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.http.MediaType;
import org.springframework.test.web.reactive.server.WebTestClient;

import static com.smarttours.gateway.security.IssuerTestSupport.AUDIENCE;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.DEV_SECRET;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_ISSUER;
import static com.smarttours.gateway.security.IssuerTestSupport.KEYCLOAK_KEY;
import static com.smarttours.gateway.security.IssuerTestSupport.claims;
import static com.smarttours.gateway.security.IssuerTestSupport.signHs256;
import static com.smarttours.gateway.security.IssuerTestSupport.signRs256;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.reactive.server.SecurityMockServerConfigurers.springSecurity;

/** Keycloak is down: its tokens can't be checked, which isn't the same as being invalid. Nothing else suffers. */
@SpringBootTest(properties = {
        "smarttours.security.dev-tokens.enabled=true",
        "smarttours.security.jwt-secret=" + DEV_SECRET,
        "smarttours.security.keycloak.issuer-uri=" + KEYCLOAK_ISSUER,
        "smarttours.security.keycloak.jwk-set-uri=http://localhost:1/certs",
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
class IssuerOutageTest {

    @Autowired
    private ApplicationContext context;

    private WebTestClient client;

    @BeforeEach
    void setUp() {
        client = WebTestClient.bindToApplicationContext(context).apply(springSecurity()).configureClient().build();
    }

    @Test
    void aKeycloakTokenGetsARetryable503NotA401() {
        String token = signRs256(KEYCLOAK_KEY, claims(KEYCLOAK_ISSUER, AUDIENCE, "CUSTOMER"));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange()
                .expectStatus().isEqualTo(503)
                .expectHeader().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON)
                .expectHeader().exists("Retry-After")
                .expectBody().jsonPath("$.status").isEqualTo(503);
    }

    @Test
    void devTokensStillWork() {
        String token = signHs256(claims(DEV_ISSUER, AUDIENCE, "CUSTOMER"));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void anonymousBrowsingStillWorks() {
        client.get().uri("/api/tours").exchange()
                .expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }
}
