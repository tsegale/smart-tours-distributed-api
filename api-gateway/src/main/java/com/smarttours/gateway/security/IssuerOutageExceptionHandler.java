package com.smarttours.gateway.security;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebExceptionHandler;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;

/**
 * The token couldn't be checked because the issuer's keys are out of reach. That says nothing about the token, so
 * the answer is a retryable 503 rather than a 401, which would tell a client to throw away a valid session.
 * Runs before Boot's default handler, which would answer 500.
 */
@Component
@Order(-2)
class IssuerOutageExceptionHandler implements WebExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(IssuerOutageExceptionHandler.class);
    private static final String PROBLEM = """
            {"type":"about:blank","title":"Service Unavailable","status":503,\
            "detail":"The token could not be verified right now. Try again shortly."}""";

    @Override
    public Mono<Void> handle(ServerWebExchange exchange, Throwable failure) {
        if (!(failure instanceof AuthenticationServiceException)) {
            return Mono.error(failure);
        }
        log.error("Could not verify a bearer token: {}", failure.getMessage());
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(HttpStatus.SERVICE_UNAVAILABLE);
        response.getHeaders().setContentType(MediaType.APPLICATION_PROBLEM_JSON);
        response.getHeaders().set("Retry-After", "5");
        DataBuffer body = response.bufferFactory().wrap(PROBLEM.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(body));
    }
}
