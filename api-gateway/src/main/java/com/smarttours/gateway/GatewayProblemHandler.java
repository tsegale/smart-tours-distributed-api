package com.smarttours.gateway;

import io.netty.channel.ConnectTimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebExceptionHandler;
import reactor.core.publisher.Mono;
import reactor.netty.channel.AbortedException;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeoutException;

/**
 * Every failure raised inside the gateway answers as RFC 9457 Problem Details, as the services behind it do. Runs before
 * Boot's default handler, whose JSON has another shape and calls a stopped service a 500, which reads as a bug in the
 * gateway and tells a client not to retry.
 *
 * An upstream that can't be reached or drops the connection is a 502, one that takes too long a 504. The detail is fixed
 * text, so no internal host name or exception message reaches a client. They go to the log, under the same requestId.
 *
 * Once the response has started the status has already gone, so nothing is written: appending JSON to a partial body would
 * corrupt it. The connection closes instead, which is how a client sees that a stream was cut short.
 */
@Component
@Order(-2)
class GatewayProblemHandler implements WebExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GatewayProblemHandler.class);

    @Override
    public Mono<Void> handle(ServerWebExchange exchange, Throwable failure) {
        ServerHttpResponse response = exchange.getResponse();
        if (response.isCommitted() || has(failure, AbortedException.class)) {
            return Mono.error(failure);
        }
        HttpStatus status = statusOf(failure);
        report(exchange.getRequest(), status, failure);

        if (failure instanceof ResponseStatusException known) {
            known.getHeaders().forEach((name, values) -> response.getHeaders().put(name, values));
        }
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_PROBLEM_JSON);
        response.getHeaders().remove(HttpHeaders.CONTENT_LENGTH);
        DataBuffer body = response.bufferFactory().wrap(problem(status, exchange.getRequest().getId()));
        return response.writeWith(Mono.just(body));
    }

    private static HttpStatus statusOf(Throwable failure) {
        if (failure instanceof ResponseStatusException known) {
            HttpStatus status = HttpStatus.resolve(known.getStatusCode().value());
            return status != null ? status : HttpStatus.INTERNAL_SERVER_ERROR;
        }
        // Before IOException: a connect timeout is one.
        if (has(failure, TimeoutException.class) || has(failure, ConnectTimeoutException.class)) {
            return HttpStatus.GATEWAY_TIMEOUT;
        }
        // Refused, unknown host, reset, closed before a response, TLS: all the connection, none of them the gateway's logic.
        if (has(failure, IOException.class)) {
            return HttpStatus.BAD_GATEWAY;
        }
        return HttpStatus.INTERNAL_SERVER_ERROR;
    }

    private static boolean has(Throwable failure, Class<? extends Throwable> type) {
        for (Throwable t = failure; t != null; t = t.getCause()) {
            if (type.isInstance(t)) {
                return true;
            }
            if (t.getCause() == t) {
                return false;
            }
        }
        return false;
    }

    private static void report(ServerHttpRequest request, HttpStatus status, Throwable failure) {
        if (status == HttpStatus.INTERNAL_SERVER_ERROR) {
            log.error("[{}] {} {} failed", request.getId(), request.getMethod(), request.getPath(), failure);
        } else if (status.is5xxServerError()) {
            log.warn("[{}] {} {} answered {}: {}", request.getId(), request.getMethod(), request.getPath(),
                    status.value(), failure.toString());
        }
    }

    private static byte[] problem(HttpStatus status, String requestId) {
        // Every value is a constant, a status code or the server's own request id, so none of them needs escaping.
        return """
                {"type":"about:blank","title":"%s","status":%d,"detail":"%s","requestId":"%s"}"""
                .formatted(status.getReasonPhrase(), status.value(), detail(status), requestId)
                .getBytes(StandardCharsets.UTF_8);
    }

    private static String detail(HttpStatus status) {
        return switch (status) {
            case BAD_GATEWAY -> "A service behind the gateway could not be reached. Try again shortly.";
            case GATEWAY_TIMEOUT -> "A service behind the gateway took too long to answer. Try again shortly.";
            case NOT_FOUND -> "Nothing here.";
            case METHOD_NOT_ALLOWED -> "This method is not supported here.";
            default -> status.is5xxServerError() ? "Something went wrong in the gateway." : "The request was refused.";
        };
    }
}
