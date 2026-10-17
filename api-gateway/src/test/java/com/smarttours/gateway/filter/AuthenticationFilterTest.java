package com.smarttours.gateway.filter;

import com.smarttours.gateway.util.JwtUtil;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Mono;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class AuthenticationFilterTest {

    @Mock
    private JwtUtil jwtUtil;

    @Mock
    private GatewayFilterChain filterChain;

    @InjectMocks
    private AuthenticationFilter authenticationFilter;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        ReflectionTestUtils.setField(authenticationFilter, "jwtUtil", jwtUtil);
        when(filterChain.filter(any())).thenReturn(Mono.empty());
    }

    @Test
    void missingAuthorizationHeader_returnsUnauthorized() {
        MockServerHttpRequest request = MockServerHttpRequest.get("/api/tours").build();
        MockServerWebExchange exchange = MockServerWebExchange.from(request);

        authenticationFilter.apply(new AuthenticationFilter.Config()).filter(exchange, filterChain).block();

        assertEquals(HttpStatus.UNAUTHORIZED, exchange.getResponse().getStatusCode());
        verify(filterChain, never()).filter(any());
    }

    @Test
    void nonBearerAuthorizationHeader_returnsUnauthorized() {
        MockServerHttpRequest request = MockServerHttpRequest.get("/api/tours")
                .header(HttpHeaders.AUTHORIZATION, "Basic dXNlcjpwYXNz")
                .build();
        MockServerWebExchange exchange = MockServerWebExchange.from(request);

        authenticationFilter.apply(new AuthenticationFilter.Config()).filter(exchange, filterChain).block();

        assertEquals(HttpStatus.UNAUTHORIZED, exchange.getResponse().getStatusCode());
        verify(filterChain, never()).filter(any());
    }

    @Test
    void invalidToken_returnsUnauthorized() {
        MockServerHttpRequest request = MockServerHttpRequest.get("/api/tours")
                .header(HttpHeaders.AUTHORIZATION, "Bearer invalid-token")
                .build();
        MockServerWebExchange exchange = MockServerWebExchange.from(request);

        doThrow(new RuntimeException("Invalid token")).when(jwtUtil).validateToken("invalid-token");

        authenticationFilter.apply(new AuthenticationFilter.Config()).filter(exchange, filterChain).block();

        assertEquals(HttpStatus.UNAUTHORIZED, exchange.getResponse().getStatusCode());
        verify(filterChain, never()).filter(any());
    }

    @Test
    void validToken_proceedsFilterChain() {
        MockServerHttpRequest request = MockServerHttpRequest.get("/api/tours")
                .header(HttpHeaders.AUTHORIZATION, "Bearer valid-token")
                .build();
        MockServerWebExchange exchange = MockServerWebExchange.from(request);

        doNothing().when(jwtUtil).validateToken("valid-token");

        authenticationFilter.apply(new AuthenticationFilter.Config()).filter(exchange, filterChain).block();

        verify(jwtUtil, times(1)).validateToken("valid-token");
        verify(filterChain, times(1)).filter(exchange);
    }
}
