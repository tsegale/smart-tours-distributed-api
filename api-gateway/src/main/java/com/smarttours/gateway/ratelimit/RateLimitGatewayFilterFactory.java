package com.smarttours.gateway.ratelimit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilter;
import org.springframework.cloud.gateway.filter.factory.AbstractGatewayFilterFactory;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RateLimiter;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.cloud.gateway.route.Route;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * Spring Cloud Gateway's Redis token bucket ({@link RedisRateLimiter}, the same Lua script the built-in
 * {@code RequestRateLimiter} filter runs), with the two things that filter does not do:
 * <ul>
 *   <li>a refusal is a 429 with an RFC 9457 Problem Details body and {@code Retry-After}, next to the
 *       limiter's {@code X-RateLimit-*} headers, instead of an empty 429;</li>
 *   <li>what happens when Redis is unreachable is chosen per route: {@code fail-mode: open} lets the
 *       request through (the built-in behaviour), {@code closed} answers 503 Problem Details.</li>
 * </ul>
 * Budgets are the standard per-route {@code redis-rate-limiter.*} arguments. Route config:
 * <pre>
 * - name: RateLimit
 *   args:
 *     fail-mode: closed
 *     redis-rate-limiter.replenishRate: 1
 *     redis-rate-limiter.burstCapacity: 5
 * </pre>
 */
public class RateLimitGatewayFilterFactory extends AbstractGatewayFilterFactory<RateLimitGatewayFilterFactory.Config> {

    public enum FailMode { OPEN, CLOSED }

    public static class Config {
        private FailMode failMode = FailMode.OPEN;

        public FailMode getFailMode() {
            return failMode;
        }

        public void setFailMode(FailMode failMode) {
            this.failMode = failMode;
        }
    }

    static final String PROBLEM_BASE = "https://smarttours.dev/problems/";

    private static final Logger log = LoggerFactory.getLogger(RateLimitGatewayFilterFactory.class);

    private final RedisRateLimiter rateLimiter;
    private final KeyResolver keyResolver;

    public RateLimitGatewayFilterFactory(RedisRateLimiter rateLimiter, KeyResolver keyResolver) {
        super(Config.class);
        this.rateLimiter = rateLimiter;
        this.keyResolver = keyResolver;
    }

    @Override
    public GatewayFilter apply(Config config) {
        return (exchange, chain) -> {
            Route route = exchange.getRequiredAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR);
            return keyResolver.resolve(exchange)
                    .defaultIfEmpty(ClientAddressResolver.UNKNOWN)
                    .flatMap(key -> rateLimiter.isAllowed(route.getId(), key)
                            // Any failure the limiter doesn't absorb itself counts as "unavailable" too, so the
                            // route's fail mode decides, never an unhandled 500.
                            .onErrorResume(ex -> {
                                log.warn("Rate limiter failed on route {}: {}", route.getId(), ex.toString());
                                return Mono.just(new RateLimiter.Response(true,
                                        Map.of(RedisRateLimiter.REMAINING_HEADER, "-1")));
                            })
                            .flatMap(response -> {
                        Map<String, String> headers = response.getHeaders();
                        if (redisUnavailable(headers)) {
                            if (config.getFailMode() == FailMode.CLOSED) {
                                log.warn("Rate limiter unavailable on route {}: failing closed", route.getId());
                                return problem(exchange, HttpStatus.SERVICE_UNAVAILABLE, "rate-limiter-unavailable",
                                        "Temporarily unavailable",
                                        "This operation is paused while its rate limiter is unavailable; retry shortly",
                                        1);
                            }
                            log.debug("Rate limiter unavailable on route {}: failing open", route.getId());
                            return chain.filter(exchange); // and no meaningless X-RateLimit-* headers
                        }
                        headers.forEach(exchange.getResponse().getHeaders()::add);
                        if (response.isAllowed()) {
                            return chain.filter(exchange);
                        }
                        return problem(exchange, HttpStatus.TOO_MANY_REQUESTS, "rate-limited", "Too many requests",
                                "Request rate limit exceeded; slow down and retry after the Retry-After interval",
                                retryAfterSeconds(route.getId()));
                    }));
        };
    }

    /**
     * RedisRateLimiter swallows Redis errors and allows the request, reporting {@code -1} tokens left
     * (its fail-open path). That is how a Redis failure is told apart from a real decision.
     */
    static boolean redisUnavailable(Map<String, String> headers) {
        return "-1".equals(headers.get(RedisRateLimiter.REMAINING_HEADER));
    }

    /** Seconds until the bucket holds enough tokens for one more request at this route's refill rate. */
    long retryAfterSeconds(String routeId) {
        RedisRateLimiter.Config limits = rateLimiter.getConfig().get(routeId);
        if (limits == null || limits.getReplenishRate() <= 0) {
            return 1;
        }
        return Math.max(1, (long) Math.ceil((double) limits.getRequestedTokens() / limits.getReplenishRate()));
    }

    static Mono<Void> problem(ServerWebExchange exchange, HttpStatus status, String slug, String title,
                              String detail, long retryAfterSeconds) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_PROBLEM_JSON);
        response.getHeaders().set(HttpHeaders.RETRY_AFTER, Long.toString(retryAfterSeconds));
        String body = """
                {"type":"%s%s","title":"%s","status":%d,"detail":"%s","instance":"%s"}"""
                .formatted(PROBLEM_BASE, slug, title, status.value(), detail,
                        jsonEscape(exchange.getRequest().getPath().value()));
        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }

    private static String jsonEscape(String value) {
        StringBuilder out = new StringBuilder(value.length());
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.toString();
    }

    @Override
    public String name() {
        return "RateLimit";
    }
}
