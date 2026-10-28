package com.smarttours.gateway.security;

import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.stream.Collectors;

/**
 * Propagates the authenticated identity to downstream services as plain headers.
 *
 * <p>Any client-supplied X-User-* header is stripped first. Without that, a caller could send
 * {@code X-User-Id: someone-else} and act as another user (an IDOR / header spoofing hole).
 */
@Component
public class UserContextHeadersFilter implements GlobalFilter, Ordered {

    public static final String USER_ID = "X-User-Id";
    public static final String USER_ROLES = "X-User-Roles";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerWebExchange sanitized = exchange.mutate()
                .request(request -> request.headers(headers -> {
                    headers.remove(USER_ID);
                    headers.remove(USER_ROLES);
                }))
                .build();

        return exchange.getPrincipal()
                .filter(JwtAuthenticationToken.class::isInstance)
                .cast(JwtAuthenticationToken.class)
                .map(auth -> sanitized.mutate()
                        .request(request -> request.headers(headers -> {
                            headers.set(USER_ID, auth.getToken().getSubject());
                            headers.set(USER_ROLES, roles(auth));
                        }))
                        .build())
                .defaultIfEmpty(sanitized)
                .flatMap(chain::filter);
    }

    private static String roles(JwtAuthenticationToken auth) {
        return auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .filter(authority -> authority.startsWith("ROLE_"))
                .map(authority -> authority.substring("ROLE_".length()))
                .collect(Collectors.joining(","));
    }

    @Override
    public int getOrder() {
        // Before the routing filters, which run at the lowest precedence.
        return 0;
    }
}
