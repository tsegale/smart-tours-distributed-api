package com.smarttours.core.integration;

import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.support.RestClientAdapter;
import org.springframework.web.service.invoker.HttpServiceProxyFactory;

import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.ZonedDateTime;

/**
 * Spring HTTP interface clients ({@code @HttpExchange}) on {@link RestClient}, one per provider, each
 * with its own connect and read timeouts, a descriptive User-Agent, and HTTP errors turned into
 * {@link ProviderHttpException} (with Retry-After parsed) so the resilience policy can classify them.
 *
 * <p>Built from Spring Boot's {@link RestClient.Builder}, so every request is observed
 * ({@code http_client_requests_seconds} by host and status).
 */
@Component
public class OutboundClients {

    /** Enough of an error body to log what the provider said, not enough to flood the logs. */
    private static final int MAX_ERROR_BODY = 300;

    private final RestClient.Builder restClients;
    private final IntegrationProperties properties;
    private final Clock clock;

    public OutboundClients(RestClient.Builder restClients, IntegrationProperties properties, Clock clock) {
        this.restClients = restClients;
        this.properties = properties;
        this.clock = clock;
    }

    public <S> S create(Class<S> api, String provider, ProviderProperties settings) {
        HttpClient http = HttpClient.newBuilder()
                .connectTimeout(settings.connectTimeout())
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
        JdkClientHttpRequestFactory requests = new JdkClientHttpRequestFactory(http);
        requests.setReadTimeout(settings.readTimeout());

        RestClient client = restClients.clone()
                .baseUrl(settings.baseUrl().toString())
                .requestFactory(requests)
                .defaultHeader(HttpHeaders.USER_AGENT, properties.userAgent())
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultStatusHandler(HttpStatusCode::isError, (request, response) -> {
                    throw new ProviderHttpException(provider, response.getStatusCode().value(),
                            Failures.parseRetryAfter(response.getHeaders().getFirst(HttpHeaders.RETRY_AFTER),
                                    ZonedDateTime.now(clock)),
                            errorBody(response.getBody()));
                })
                .build();
        return HttpServiceProxyFactory.builderFor(RestClientAdapter.create(client)).build().createClient(api);
    }

    /** The response is closed by RestClient after this handler, so the rest of the body needn't be read. */
    private static String errorBody(InputStream body) {
        try {
            byte[] bytes = body.readNBytes(MAX_ERROR_BODY);
            return new String(bytes, StandardCharsets.UTF_8).replaceAll("\\s+", " ").strip();
        } catch (IOException unreadable) {
            return "(body unreadable: " + unreadable.getMessage() + ")";
        }
    }
}
