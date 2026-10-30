package com.smarttours.gateway.ratelimit;

import org.junit.jupiter.api.Test;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import java.net.InetSocketAddress;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ClientAddressResolverTest {

    private final ClientAddressResolver resolver = new ClientAddressResolver(List.of("10.0.0.0/8", "192.168.1.7"));

    @Test
    void untrustedPeer_isTheClient_whateverItsHeadersSay() {
        assertThat(resolve("198.51.100.4", "203.0.113.9")).isEqualTo("198.51.100.4");
    }

    @Test
    void trustedProxy_rightmostUntrustedHopIsTheClient() {
        assertThat(resolve("10.1.2.3", "1.1.1.1, 203.0.113.9")).isEqualTo("203.0.113.9");
        assertThat(resolve("10.1.2.3", "203.0.113.9, 10.9.9.9")).isEqualTo("203.0.113.9");
        assertThat(resolve("192.168.1.7", "203.0.113.9")).isEqualTo("203.0.113.9");
    }

    @Test
    void forgedNonAddressHops_areNotTrusted() {
        assertThat(resolve("10.1.2.3", "evil.example")).isEqualTo("evil.example");
    }

    @Test
    void onlyProxies_meansTheRequestStartedInside() {
        assertThat(resolve("10.1.2.3", "10.4.4.4")).isEqualTo("10.1.2.3");
        assertThat(resolve("10.1.2.3", null)).isEqualTo("10.1.2.3");
    }

    @Test
    void cidrMatching_respectsPartialBytes() {
        ClientAddressResolver.Cidr block = ClientAddressResolver.Cidr.parse("172.16.0.0/12");
        assertThat(block.contains(ClientAddressResolver.parseLiteral("172.31.255.1"))).isTrue();
        assertThat(block.contains(ClientAddressResolver.parseLiteral("172.32.0.1"))).isFalse();
        assertThat(block.contains(ClientAddressResolver.parseLiteral("::1"))).isFalse();
        assertThatThrownBy(() -> ClientAddressResolver.Cidr.parse("10.0.0.0/33"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private String resolve(String peer, String forwardedFor) {
        MockServerHttpRequest.BaseBuilder<?> request = MockServerHttpRequest.get("/")
                .remoteAddress(new InetSocketAddress(peer, 40000));
        if (forwardedFor != null) {
            request.header("X-Forwarded-For", forwardedFor);
        }
        return resolver.resolve(MockServerWebExchange.from(request));
    }
}
