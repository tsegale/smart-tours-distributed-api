package com.smarttours.gateway.util;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Date;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class JwtUtilTest {

    private JwtUtil jwtUtil;
    private final String secret = "3cfa76ef14937c1c0ea519f8fc057a80fcd04a7420f8e8bcd0a7567c272e007b";

    @BeforeEach
    void setUp() {
        jwtUtil = new JwtUtil();
        ReflectionTestUtils.setField(jwtUtil, "secret", secret);
    }

    @Test
    void validateToken_validToken_doesNotThrow() {
        byte[] keyBytes = Decoders.BASE64.decode(secret);
        String token = Jwts.builder()
                .setSubject("user-123")
                .setIssuedAt(new Date())
                .setExpiration(new Date(System.currentTimeMillis() + 60000))
                .signWith(Keys.hmacShaKeyFor(keyBytes), SignatureAlgorithm.HS256)
                .compact();

        assertDoesNotThrow(() -> jwtUtil.validateToken(token));
    }

    @Test
    void validateToken_expiredToken_throwsException() {
        byte[] keyBytes = Decoders.BASE64.decode(secret);
        String expiredToken = Jwts.builder()
                .setSubject("user-123")
                .setIssuedAt(new Date(System.currentTimeMillis() - 120000))
                .setExpiration(new Date(System.currentTimeMillis() - 60000))
                .signWith(Keys.hmacShaKeyFor(keyBytes), SignatureAlgorithm.HS256)
                .compact();

        assertThrows(Exception.class, () -> jwtUtil.validateToken(expiredToken));
    }

    @Test
    void validateToken_invalidSignature_throwsException() {
        String invalidSecret = "1111222233334444555566667777888899990000111122223333444455556666";
        byte[] keyBytes = Decoders.BASE64.decode(invalidSecret);
        String tokenWithWrongKey = Jwts.builder()
                .setSubject("user-123")
                .setIssuedAt(new Date())
                .setExpiration(new Date(System.currentTimeMillis() + 60000))
                .signWith(Keys.hmacShaKeyFor(keyBytes), SignatureAlgorithm.HS256)
                .compact();

        assertThrows(Exception.class, () -> jwtUtil.validateToken(tokenWithWrongKey));
    }
}
