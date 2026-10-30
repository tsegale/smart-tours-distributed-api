package com.smarttours.gateway.ratelimit;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.Date;
import java.util.List;

/** Shared bits for the rate-limit tests: an upstream that always answers 200, and signed tokens. */
final class RateLimitTestSupport {

    // 48 random bytes, base64 encoded. Test-only.
    static final String SECRET = "qS0t8yQ0bX9mJ3vV6cP2nL5kR8wE1zT4uY7iO0pA3sD6fG9hJ2kL5zX8cV1bN4mQ";

    private RateLimitTestSupport() {
    }

    static HttpServer okUpstream() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.createContext("/", exchange -> {
                exchange.getRequestBody().readAllBytes();
                byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    static String bearer(String subject) {
        try {
            JWTClaimsSet claims = new JWTClaimsSet.Builder()
                    .subject(subject)
                    .claim("roles", List.of("CUSTOMER"))
                    .issueTime(new Date())
                    .expirationTime(Date.from(Instant.now().plusSeconds(300)))
                    .build();
            SignedJWT jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims);
            jwt.sign(new MACSigner(Base64.getDecoder().decode(SECRET)));
            return "Bearer " + jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
