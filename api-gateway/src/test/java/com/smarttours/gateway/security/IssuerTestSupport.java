package com.smarttours.gateway.security;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.JWSSigner;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
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

/** Tokens from both issuers, and an endpoint standing in for Keycloak's key set. */
final class IssuerTestSupport {

    // 48 random bytes, base64 encoded. Test-only.
    static final String DEV_SECRET = "qS0t8yQ0bX9mJ3vV6cP2nL5kR8wE1zT4uY7iO0pA3sD6fG9hJ2kL5zX8cV1bN4mQ";
    static final String DEV_ISSUER = "smart-tours-dev";
    static final String KEYCLOAK_ISSUER = "https://auth.example.test/realms/smart-tours";
    static final String AUDIENCE = "smart-tours-api";
    static final RSAKey KEYCLOAK_KEY = rsaKey("keycloak-key");

    private IssuerTestSupport() {
    }

    static RSAKey rsaKey(String keyId) {
        try {
            return new RSAKeyGenerator(2048).keyID(keyId).generate();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** Serves only the public part of the key, as Keycloak's certs endpoint does. */
    static HttpServer jwksEndpoint(RSAKey key) {
        try {
            byte[] body = new JWKSet(key.toPublicJWK()).toString().getBytes(StandardCharsets.UTF_8);
            HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
            server.createContext("/certs", exchange -> {
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

    static JWTClaimsSet.Builder claims(String issuer, String audience, String... roles) {
        JWTClaimsSet.Builder builder = new JWTClaimsSet.Builder()
                .subject("user-1")
                .claim(SecurityConfig.ROLES_CLAIM, List.of(roles))
                .issueTime(new Date())
                .expirationTime(Date.from(Instant.now().plusSeconds(60)));
        if (issuer != null) {
            builder.issuer(issuer);
        }
        if (audience != null) {
            builder.audience(audience);
        }
        return builder;
    }

    static String signRs256(RSAKey key, JWTClaimsSet.Builder claims) {
        try {
            JWSHeader header = new JWSHeader.Builder(JWSAlgorithm.RS256)
                    .keyID(key.getKeyID()).type(JOSEObjectType.JWT).build();
            return sign(header, new RSASSASigner(key), claims);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String signHs256(JWTClaimsSet.Builder claims) {
        try {
            return signHs256(Base64.getDecoder().decode(DEV_SECRET), claims);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String signHs256(byte[] secret, JWTClaimsSet.Builder claims) {
        try {
            return sign(new JWSHeader(JWSAlgorithm.HS256), new MACSigner(secret), claims);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static String sign(JWSHeader header, JWSSigner signer, JWTClaimsSet.Builder claims) throws Exception {
        SignedJWT jwt = new SignedJWT(header, claims.build());
        jwt.sign(signer);
        return jwt.serialize();
    }
}
