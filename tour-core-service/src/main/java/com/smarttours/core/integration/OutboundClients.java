package com.smarttours.core.integration;

import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpRequest;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.client.support.HttpRequestWrapper;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.support.RestClientAdapter;
import org.springframework.web.service.invoker.HttpServiceProxyFactory;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLEncoder;
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
        return create(api, provider, settings.baseUrl(), settings, null);
    }

    /**
     * For providers that take their API key in the URL (Open-Meteo): {@code key} is appended to every request's
     * query string. Spring leaves the query string out of its I/O error messages, so the key doesn't reach the
     * logs through them (tested).
     */
    public <S> S create(Class<S> api, String provider, URI baseUrl, ProviderProperties settings, QueryKey key) {
        HttpClient http = HttpClient.newBuilder()
                .connectTimeout(settings.connectTimeout())
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
        JdkClientHttpRequestFactory requests = new JdkClientHttpRequestFactory(http);
        requests.setReadTimeout(settings.readTimeout());

        RestClient.Builder builder = restClients.clone();
        if (key != null) {
            builder.requestInterceptor((request, body, execution) -> execution.execute(key.appendTo(request), body));
        }
        RestClient client = builder
                .baseUrl(baseUrl.toString())
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

    /** An API key sent as a query parameter. Its {@code toString} hides the value. */
    public record QueryKey(String name, String value) {

        @Override
        public String toString() {
            return "QueryKey[" + name + "=(redacted)]";
        }

        HttpRequest appendTo(HttpRequest request) {
            URI uri = request.getURI();
            String parameter = URLEncoder.encode(name, StandardCharsets.UTF_8) + "="
                    + URLEncoder.encode(value, StandardCharsets.UTF_8);
            URI keyed = URI.create(uri + (uri.getRawQuery() == null ? "?" : "&") + parameter);
            return new HttpRequestWrapper(request) {
                @Override
                public URI getURI() {
                    return keyed;
                }
            };
        }
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
