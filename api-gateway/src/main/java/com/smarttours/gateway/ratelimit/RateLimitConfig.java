package com.smarttours.gateway.ratelimit;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.TimeoutOptions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.data.redis.autoconfigure.LettuceClientConfigurationBuilderCustomizer;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import reactor.core.publisher.Mono;

import java.util.List;

/**
 * Rate limiting at the edge (ADR 0010): Spring Cloud Gateway's Redis token bucket, keyed by who is calling.
 */
@Configuration
public class RateLimitConfig {

    /**
     * {@code user:<jwt subject>} for authenticated calls, else {@code ip:<client address>}. A user keeps
     * one budget across every address they call from; anonymous callers share one per address.
     */
    @Bean
    @Primary
    KeyResolver callerKeyResolver(ClientAddressResolver addresses) {
        return exchange -> exchange.getPrincipal()
                .filter(JwtAuthenticationToken.class::isInstance)
                .map(principal -> "user:" + ((JwtAuthenticationToken) principal).getToken().getSubject())
                .switchIfEmpty(Mono.fromSupplier(() -> "ip:" + addresses.resolve(exchange)));
    }

    /** The {@code RateLimit} route filter: Redis token bucket, Problem Details, per-route fail mode. */
    @Bean
    RateLimitGatewayFilterFactory rateLimitGatewayFilterFactory(RedisRateLimiter redisRateLimiter,
                                                                KeyResolver callerKeyResolver) {
        return new RateLimitGatewayFilterFactory(redisRateLimiter, callerKeyResolver);
    }

    @Bean
    ClientAddressResolver clientAddressResolver(
            @Value("${smarttours.rate-limit.trusted-proxies:}") List<String> trustedProxies) {
        return new ClientAddressResolver(trustedProxies);
    }

    /**
     * When Redis is unreachable, fail the command at once instead of queueing it until the timeout:
     * the limiter then decides fail-open or fail-closed immediately, per route.
     */
    @Bean
    LettuceClientConfigurationBuilderCustomizer failFastWhenRedisIsDown() {
        return builder -> builder.clientOptions(ClientOptions.builder()
                .disconnectedBehavior(ClientOptions.DisconnectedBehavior.REJECT_COMMANDS)
                .autoReconnect(true)
                .timeoutOptions(TimeoutOptions.enabled())
                .build());
    }
}
