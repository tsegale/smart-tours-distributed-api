package com.smarttours.gateway.security;

import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.cloud.gateway.route.Route;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
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
 *
 * <p>{@code X-User-Email} carries the token's {@code email} claim when it has a usable one (booking-service
 * snapshots it as the contact address for notifications). A token without one simply sends no header.
 */
@Component
public class UserContextHeadersFilter implements GlobalFilter, Ordered {

    public static final String USER_ID = "X-User-Id";
    public static final String USER_ROLES = "X-User-Roles";
    public static final String USER_EMAIL = "X-User-Email";
    static final String EMAIL_CLAIM = "email";
    /** RFC 5321's limit on a forward path; anything longer isn't a deliverable address. */
    static final int MAX_EMAIL_LENGTH = 320;
    public static final String IDENTITY_PROPAGATION = "identity-propagation";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerWebExchange sanitized = exchange.mutate()
                .request(request -> request.headers(headers -> {
                    headers.remove(USER_ID);
                    headers.remove(USER_ROLES);
                    headers.remove(USER_EMAIL);
                }))
                .build();

        if (!propagatesIdentity(exchange)) {
            return chain.filter(sanitized);
        }

        return exchange.getPrincipal()
                .filter(JwtAuthenticationToken.class::isInstance)
                .cast(JwtAuthenticationToken.class)
                .map(auth -> sanitized.mutate()
                        .request(request -> request.headers(headers -> {
                            headers.set(USER_ID, auth.getToken().getSubject());
                            headers.set(USER_ROLES, roles(auth));
                            String email = email(auth);
                            if (email != null) {
                                headers.set(USER_EMAIL, email);
                            }
                        }))
                        .build())
                .defaultIfEmpty(sanitized)
                .flatMap(chain::filter);
    }

    /**
     * Routes opt out with {@code metadata.identity-propagation: false} (the public webhook route).
     * Spoofed X-User-* headers are still stripped on those routes.
     */
    static boolean propagatesIdentity(ServerWebExchange exchange) {
        Route route = exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR);
        if (route == null) {
            return true;
        }
        Object flag = route.getMetadata().get(IDENTITY_PROPAGATION);
        return flag == null || Boolean.parseBoolean(flag.toString());
    }

    /** The email claim if it is a plausible address: one '@', no whitespace or control characters, not too long. */
    static String email(JwtAuthenticationToken auth) {
        Object claim = auth.getToken().getClaims().get(EMAIL_CLAIM);
        if (!(claim instanceof String email)) {
            return null;
        }
        String trimmed = email.strip();
        int at = trimmed.indexOf('@');
        boolean plausible = !trimmed.isEmpty() && trimmed.length() <= MAX_EMAIL_LENGTH
                && at > 0 && at == trimmed.lastIndexOf('@') && at < trimmed.length() - 1
                && trimmed.chars().noneMatch(c -> Character.isWhitespace(c) || Character.isISOControl(c));
        return plausible ? trimmed : null;
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
