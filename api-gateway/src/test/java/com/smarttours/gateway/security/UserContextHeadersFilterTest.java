package com.smarttours.gateway.security;

import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class UserContextHeadersFilterTest {

    private final UserContextHeadersFilter filter = new UserContextHeadersFilter();

    @Test
    void authenticatedRequest_getsIdentityHeadersFromToken_andSpoofedValuesAreReplaced() {
        MockServerWebExchange original = MockServerWebExchange.from(MockServerHttpRequest.post("/api/bookings")
                .header(UserContextHeadersFilter.USER_ID, "victim-user")
                .header(UserContextHeadersFilter.USER_ROLES, "ADMIN"));
        ServerWebExchange exchange = original.mutate().principal(Mono.just(token("alice", "CUSTOMER"))).build();

        ServerWebExchange forwarded = run(exchange);

        assertThat(forwarded.getRequest().getHeaders().get(UserContextHeadersFilter.USER_ID))
                .containsExactly("alice");
        assertThat(forwarded.getRequest().getHeaders().get(UserContextHeadersFilter.USER_ROLES))
                .containsExactly("CUSTOMER");
    }

    @Test
    void anonymousRequest_hasClientSuppliedIdentityHeadersStripped() {
        MockServerWebExchange exchange = MockServerWebExchange.from(MockServerHttpRequest.get("/api/tours")
                .header(UserContextHeadersFilter.USER_ID, "victim-user")
                .header(UserContextHeadersFilter.USER_ROLES, "ADMIN"));

        ServerWebExchange forwarded = run(exchange);

        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ID)).isFalse();
        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ROLES)).isFalse();
    }

    private ServerWebExchange run(ServerWebExchange exchange) {
        AtomicReference<ServerWebExchange> captured = new AtomicReference<>();
        GatewayFilterChain chain = forwarded -> {
            captured.set(forwarded);
            return Mono.empty();
        };
        filter.filter(exchange, chain).block();
        return captured.get();
    }

    private static JwtAuthenticationToken token(String subject, String role) {
        Jwt jwt = Jwt.withTokenValue("test-token").header("alg", "HS256").subject(subject).build();
        return new JwtAuthenticationToken(jwt, List.of(new SimpleGrantedAuthority("ROLE_" + role)));
    }
}
