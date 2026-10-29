package com.smarttours.gateway.security;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.time.Instant;
import java.util.Base64;
import java.util.Date;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.reactive.server.SecurityMockServerConfigurers.springSecurity;

/**
 * Exercises the real security chain. Downstream services are not running, so a request that
 * passes security fails later with a 5xx. These tests only assert the auth decision (401/403 or not).
 */
@SpringBootTest(properties = {
        "smarttours.security.jwt-secret=" + SecurityConfigTest.SECRET,
        "TOUR_SERVICE_URL=http://localhost:1",
        "BOOKING_SERVICE_URL=http://localhost:1",
        "AI_SERVICE_URL=http://localhost:1"
})
class SecurityConfigTest {

    // 48 random bytes, base64 encoded. Test-only; never used outside this class.
    static final String SECRET = "qS0t8yQ0bX9mJ3vV6cP2nL5kR8wE1zT4uY7iO0pA3sD6fG9hJ2kL5zX8cV1bN4mQ";

    @Autowired
    private ApplicationContext context;

    private WebTestClient client;

    @BeforeEach
    void setUp() {
        client = WebTestClient.bindToApplicationContext(context).apply(springSecurity()).configureClient().build();
    }

    @Test
    void bookingsWithoutToken_isRejected() {
        client.get().uri("/api/bookings/me").exchange().expectStatus().isUnauthorized();
    }

    @Test
    void bookingsWithTamperedToken_isRejected() {
        String token = sign("alice", List.of("CUSTOMER"), Instant.now().plusSeconds(60)) + "x";
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().isUnauthorized();
    }

    @Test
    void bookingsWithExpiredToken_isRejected() {
        String token = sign("alice", List.of("CUSTOMER"), Instant.now().minusSeconds(3600));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().isUnauthorized();
    }

    @Test
    void bookingsWithValidToken_passesSecurity() {
        String token = sign("alice", List.of("CUSTOMER"), Instant.now().plusSeconds(60));
        client.get().uri("/api/bookings/me").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void browsingToursIsPublic() {
        client.get().uri("/api/tours").exchange()
                .expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void creatingTourAsCustomer_isForbidden() {
        String token = sign("alice", List.of("CUSTOMER"), Instant.now().plusSeconds(60));
        client.post().uri("/api/tours").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().isForbidden();
    }

    @Test
    void creatingTourAsOperator_passesSecurity() {
        String token = sign("op-1", List.of("OPERATOR"), Instant.now().plusSeconds(60));
        client.post().uri("/api/tours").header("Authorization", "Bearer " + token)
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void stripeWebhookWithoutToken_passesSecurity() {
        client.post().uri("/webhooks/stripe").bodyValue("{}").exchange()
                .expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void stripeWebhookWithGarbageBearer_isNotRejectedByTheGateway() {
        // The webhook chain has no bearer-token filter; booking-service checks the signature instead.
        client.post().uri("/webhooks/stripe").header("Authorization", "Bearer not-a-jwt").bodyValue("{}")
                .exchange().expectStatus().value(status -> assertThat(status).isNotIn(401, 403));
    }

    @Test
    void otherMethodsOnWebhookPaths_areDenied() {
        client.get().uri("/webhooks/stripe").exchange()
                .expectStatus().value(status -> assertThat(status).isIn(401, 403));
    }

    @Test
    void healthIsPublic() {
        client.get().uri("/actuator/health").exchange().expectStatus().isOk();
    }

    private static String sign(String subject, List<String> roles, Instant expiresAt) {
        try {
            JWTClaimsSet claims = new JWTClaimsSet.Builder()
                    .subject(subject)
                    .claim(SecurityConfig.ROLES_CLAIM, roles)
                    .issueTime(new Date())
                    .expirationTime(Date.from(expiresAt))
                    .build();
            SignedJWT jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims);
            jwt.sign(new MACSigner(Base64.getDecoder().decode(SECRET)));
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
