package com.smarttours.gateway.security;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.http.HttpMethod;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.authentication.ReactiveAuthenticationManager;
import org.springframework.security.config.annotation.web.reactive.EnableWebFluxSecurity;
import org.springframework.security.config.web.server.ServerHttpSecurity;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtIssuerReactiveAuthenticationManagerResolver;
import org.springframework.security.oauth2.server.resource.authentication.ReactiveJwtAuthenticationConverterAdapter;
import org.springframework.security.web.server.SecurityWebFilterChain;
import org.springframework.security.web.server.context.NoOpServerSecurityContextRepository;
import org.springframework.security.web.server.util.matcher.ServerWebExchangeMatchers;
import org.springframework.core.convert.converter.Converter;
import reactor.core.publisher.Mono;

import java.util.Map;

/**
 * Edge security. The gateway is the only component that sees raw tokens. Downstream services
 * trust the X-User-Id / X-User-Roles headers it sets (see {@link UserContextHeadersFilter}),
 * which is safe only because they are not reachable from outside the compose network.
 */
@Configuration
@EnableWebFluxSecurity
@EnableConfigurationProperties(SecurityProperties.class)
public class SecurityConfig {

    static final String ROLES_CLAIM = "roles";
    static final String WEBHOOK_PATHS = "/webhooks/**";

    /**
     * Payment-provider webhooks. Matched first and kept apart from the main chain: there is no
     * bearer-token filter at all here, so a stray or invalid Authorization header can't turn a
     * provider's delivery into a 401. booking-service authenticates the call by verifying the
     * provider's signature over the raw body.
     */
    @Bean
    @Order(Ordered.HIGHEST_PRECEDENCE)
    SecurityWebFilterChain webhookSecurityWebFilterChain(ServerHttpSecurity http) {
        return http
                .securityMatcher(ServerWebExchangeMatchers.pathMatchers(WEBHOOK_PATHS))
                .csrf(ServerHttpSecurity.CsrfSpec::disable)
                .httpBasic(ServerHttpSecurity.HttpBasicSpec::disable)
                .formLogin(ServerHttpSecurity.FormLoginSpec::disable)
                .requestCache(ServerHttpSecurity.RequestCacheSpec::disable)
                .securityContextRepository(NoOpServerSecurityContextRepository.getInstance())
                .authorizeExchange(exchanges -> exchanges
                        .pathMatchers(HttpMethod.POST, WEBHOOK_PATHS).permitAll()
                        .anyExchange().denyAll())
                .build();
    }

    @Bean
    SecurityWebFilterChain securityWebFilterChain(
            ServerHttpSecurity http, JwtIssuerReactiveAuthenticationManagerResolver issuers) {
        return http
                // Stateless bearer-token API: no cookies, so CSRF protection does not apply.
                .csrf(ServerHttpSecurity.CsrfSpec::disable)
                .httpBasic(ServerHttpSecurity.HttpBasicSpec::disable)
                .formLogin(ServerHttpSecurity.FormLoginSpec::disable)
                .authorizeExchange(exchanges -> exchanges
                        .pathMatchers("/actuator/health/**", "/actuator/info").permitAll()
                        // Browsing the catalogue is public; changing it is for tour operators.
                        .pathMatchers(HttpMethod.GET, "/api/tours", "/api/tours/**").permitAll()
                        .pathMatchers(HttpMethod.GET, "/docs/tours/**").permitAll()
                        .pathMatchers("/api/tours", "/api/tours/**").hasAnyRole("OPERATOR", "ADMIN")
                        .pathMatchers(HttpMethod.POST, "/api/availability").hasAnyRole("OPERATOR", "ADMIN")
                        .pathMatchers("/api/check-ins", "/api/check-ins/**").hasAnyRole("OPERATOR", "ADMIN")
                        .pathMatchers(HttpMethod.POST, "/api/ai/embed").hasRole("ADMIN")
                        .anyExchange().authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.authenticationManagerResolver(issuers))
                .build();
    }

    /**
     * Only the issuers in the map are trusted. An unknown or missing {@code iss} is a 401, and no key is ever
     * fetched from a location a token names (ADR 0016).
     */
    @Bean
    JwtIssuerReactiveAuthenticationManagerResolver issuerResolver(SecurityProperties properties, Environment environment) {
        boolean production = environment.acceptsProfiles(Profiles.of(TokenIssuers.PRODUCTION_PROFILE));
        Map<String, ReactiveAuthenticationManager> managers =
                TokenIssuers.managers(properties, production, jwtAuthenticationConverter());
        return new JwtIssuerReactiveAuthenticationManagerResolver(issuer -> Mono.justOrEmpty(managers.get(issuer)));
    }

    /** Maps the "roles" claim (e.g. ["CUSTOMER"]) to Spring authorities ROLE_CUSTOMER. */
    static Converter<Jwt, Mono<AbstractAuthenticationToken>> jwtAuthenticationConverter() {
        JwtGrantedAuthoritiesConverter authorities = new JwtGrantedAuthoritiesConverter();
        authorities.setAuthoritiesClaimName(ROLES_CLAIM);
        authorities.setAuthorityPrefix("ROLE_");

        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(authorities);
        return new ReactiveJwtAuthenticationConverterAdapter(converter);
    }
}
