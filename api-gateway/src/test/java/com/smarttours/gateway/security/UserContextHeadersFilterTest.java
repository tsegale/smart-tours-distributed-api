package com.smarttours.gateway.security;

import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.route.Route;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.net.URI;
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
    void emailClaim_isForwardedAndASpoofedEmailHeaderIsReplaced() {
        MockServerWebExchange original = MockServerWebExchange.from(MockServerHttpRequest.post("/api/bookings")
                .header(UserContextHeadersFilter.USER_EMAIL, "victim@example.com"));
        ServerWebExchange exchange = original.mutate()
                .principal(Mono.just(token("alice", "CUSTOMER", "alice@example.com"))).build();

        ServerWebExchange forwarded = run(exchange);

        assertThat(forwarded.getRequest().getHeaders().get(UserContextHeadersFilter.USER_EMAIL))
                .containsExactly("alice@example.com");
    }

    @Test
    void tokenWithoutAUsableEmail_sendsNoEmailHeader_evenIfTheClientSentOne() {
        for (String claim : new String[] {null, "", "not-an-address", "a@b@c", "eve@example.com\r\nX-User-Id: x",
                "two words@example.com"}) {
            MockServerWebExchange original = MockServerWebExchange.from(MockServerHttpRequest.post("/api/bookings")
                    .header(UserContextHeadersFilter.USER_EMAIL, "victim@example.com"));
            ServerWebExchange exchange = original.mutate()
                    .principal(Mono.just(token("alice", "CUSTOMER", claim))).build();

            ServerWebExchange forwarded = run(exchange);

            assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_EMAIL))
                    .as("claim %s", claim).isFalse();
        }
    }

    @Test
    void anonymousRequest_hasClientSuppliedIdentityHeadersStripped() {
        MockServerWebExchange exchange = MockServerWebExchange.from(MockServerHttpRequest.get("/api/tours")
                .header(UserContextHeadersFilter.USER_ID, "victim-user")
                .header(UserContextHeadersFilter.USER_ROLES, "ADMIN")
                .header(UserContextHeadersFilter.USER_EMAIL, "victim@example.com"));

        ServerWebExchange forwarded = run(exchange);

        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ID)).isFalse();
        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ROLES)).isFalse();
        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_EMAIL)).isFalse();
    }

    @Test
    void routeThatOptsOut_getsNoIdentityHeaders_evenWhenAuthenticated_andSpoofedOnesAreStripped() {
        MockServerWebExchange original = MockServerWebExchange.from(MockServerHttpRequest.post("/webhooks/stripe")
                .header(UserContextHeadersFilter.USER_ID, "victim-user")
                .header(UserContextHeadersFilter.USER_ROLES, "ADMIN"));
        ServerWebExchange exchange = original.mutate().principal(Mono.just(token("alice", "CUSTOMER"))).build();
        exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR, route(false));

        ServerWebExchange forwarded = run(exchange);

        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ID)).isFalse();
        assertThat(forwarded.getRequest().getHeaders().containsHeader(UserContextHeadersFilter.USER_ROLES)).isFalse();
    }

    @Test
    void routesPropagateIdentityUnlessTheyOptOut() {
        MockServerWebExchange exchange = MockServerWebExchange.from(MockServerHttpRequest.get("/api/bookings/me"));
        assertThat(UserContextHeadersFilter.propagatesIdentity(exchange)).isTrue();

        exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR, route(true));
        assertThat(UserContextHeadersFilter.propagatesIdentity(exchange)).isTrue();

        exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR, route(false));
        assertThat(UserContextHeadersFilter.propagatesIdentity(exchange)).isFalse();
    }

    private static Route route(boolean identityPropagation) {
        return Route.async()
                .id("test-route")
                .uri(URI.create("http://localhost:1"))
                .predicate(exchange -> true)
                .metadata(UserContextHeadersFilter.IDENTITY_PROPAGATION, identityPropagation)
                .build();
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
        return token(subject, role, null);
    }

    private static JwtAuthenticationToken token(String subject, String role, String email) {
        Jwt.Builder jwt = Jwt.withTokenValue("test-token").header("alg", "HS256").subject(subject);
        if (email != null) {
            jwt.claim("email", email);
        }
        return new JwtAuthenticationToken(jwt.build(), List.of(new SimpleGrantedAuthority("ROLE_" + role)));
    }
}
