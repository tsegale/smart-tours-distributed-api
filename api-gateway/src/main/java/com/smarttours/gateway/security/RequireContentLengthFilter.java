package com.smarttours.gateway.security;

import org.springframework.core.Ordered;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/**
 * Closes the gap in the request-size limits: the gateway's RequestSize filter reads only the Content-Length header, so
 * a chunked body, which has none, would pass any limit. No client of this API needs chunked uploads (browsers, Stripe
 * and scripts all send a length), so such a request is refused with 411 Length Required.
 */
@Component
class RequireContentLengthFilter implements WebFilter, Ordered {

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        HttpHeaders headers = request.getHeaders();
        if (headers.containsHeader(HttpHeaders.TRANSFER_ENCODING) && headers.getContentLength() < 0) {
            return Mono.error(new ResponseStatusException(
                    HttpStatus.LENGTH_REQUIRED, "Send a Content-Length: chunked request bodies are not accepted"));
        }
        return chain.filter(exchange);
    }

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
