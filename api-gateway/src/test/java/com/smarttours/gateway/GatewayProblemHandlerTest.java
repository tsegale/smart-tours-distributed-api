package com.smarttours.gateway;

import io.netty.channel.ConnectTimeoutException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.MethodNotAllowedException;
import reactor.netty.channel.AbortedException;

import java.net.UnknownHostException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** The branches a real upstream can't reach on demand. */
class GatewayProblemHandlerTest {

    private final GatewayProblemHandler handler = new GatewayProblemHandler();

    private static MockServerWebExchange exchange() {
        return MockServerWebExchange.from(MockServerHttpRequest.get("/anything"));
    }

    @Test
    void aBugInTheGatewayIsA500ThatKeepsItsMessagePrivate() {
        MockServerWebExchange exchange = exchange();
        handler.handle(exchange, new IllegalStateException("db password is hunter2")).block();

        assertThat(exchange.getResponse().getStatusCode().value()).isEqualTo(500);
        assertThat(exchange.getResponse().getHeaders().getContentType()).isEqualTo(MediaType.APPLICATION_PROBLEM_JSON);
        assertThat(exchange.getResponse().getBodyAsString().block())
                .contains("\"status\":500")
                .doesNotContain("hunter2");
    }

    /** How a stopped container looks from inside Compose. A real lookup's timing depends on the machine's DNS. */
    @Test
    void aHostNameThatIsGoneIsA502AndTheNameStaysPrivate() {
        MockServerWebExchange exchange = exchange();
        handler.handle(exchange, new UnknownHostException("ai-service")).block();

        assertThat(exchange.getResponse().getStatusCode().value()).isEqualTo(502);
        assertThat(exchange.getResponse().getBodyAsString().block()).doesNotContain("ai-service");
    }

    @Test
    void aConnectTimeoutIsA504ThoughItIsAlsoAnIoFailure() {
        MockServerWebExchange exchange = exchange();
        handler.handle(exchange, new ConnectTimeoutException("connect timed out")).block();

        assertThat(exchange.getResponse().getStatusCode().value()).isEqualTo(504);
    }

    @Test
    void aClientThatHungUpGetsNothingWritten() {
        MockServerWebExchange exchange = exchange();
        AbortedException gone = new AbortedException("client went away");

        assertThatThrownBy(() -> handler.handle(exchange, gone).block()).isSameAs(gone);
        assertThat(exchange.getResponse().getStatusCode()).isNull();
    }

    @Test
    void aStartedResponseIsLeftAlone() {
        MockServerWebExchange exchange = exchange();
        exchange.getResponse().setComplete().block();
        IllegalStateException failure = new IllegalStateException("mid-stream");

        assertThatThrownBy(() -> handler.handle(exchange, failure).block()).isSameAs(failure);
    }

    @Test
    void headersTheErrorRequiresAreKept() {
        MockServerWebExchange exchange = exchange();
        handler.handle(exchange, new MethodNotAllowedException(HttpMethod.DELETE, List.of(HttpMethod.GET))).block();

        assertThat(exchange.getResponse().getStatusCode().value()).isEqualTo(405);
        assertThat(exchange.getResponse().getHeaders().getAllow()).containsExactly(HttpMethod.GET);
    }
}
