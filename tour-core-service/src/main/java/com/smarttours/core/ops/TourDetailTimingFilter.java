package com.smarttours.core.ops;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.MethodParameter;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

import java.io.IOException;

/**
 * Times {@code GET /api/tours/{id}} as a whole, and the part after the controller returned: serialising the
 * body and handing it to Tomcat. The first filter in the chain, so the request timer covers every other filter.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TourDetailTimingFilter extends OncePerRequestFilter {

    static final String DETAIL_PATTERN = "/api/tours/{id}";
    static final String WRITE_STARTED = TourDetailTimingFilter.class.getName() + ".writeStarted";

    private final TourDetailStages stages;

    public TourDetailTimingFilter(TourDetailStages stages) {
        this.stages = stages;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        long started = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            if ("GET".equals(request.getMethod())
                    && DETAIL_PATTERN.equals(request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE))) {
                long ended = System.nanoTime();
                stages.recordRequest(ended - started);
                if (request.getAttribute(WRITE_STARTED) instanceof Long writeStarted) {
                    stages.recordWrite(ended - writeStarted);
                }
            }
        }
    }

    /** Marks the moment the controller's return value goes to the message converter. */
    @ControllerAdvice
    static class WriteStartMarker implements ResponseBodyAdvice<Object> {

        @Override
        public boolean supports(MethodParameter returnType, Class<? extends HttpMessageConverter<?>> converterType) {
            return true;
        }

        @Override
        public Object beforeBodyWrite(Object body, MethodParameter returnType, MediaType selectedContentType,
                                      Class<? extends HttpMessageConverter<?>> selectedConverterType,
                                      ServerHttpRequest request, ServerHttpResponse response) {
            if (request instanceof ServletServerHttpRequest servlet) {
                servlet.getServletRequest().setAttribute(WRITE_STARTED, System.nanoTime());
            }
            return body;
        }
    }
}
