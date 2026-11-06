package com.smarttours.gateway;

import org.junit.jupiter.api.Test;
import reactor.netty.http.client.HttpClient;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Only that the client gets its own resolver: reactor-netty doesn't expose the TTL, and proving the effect takes a container
 * changing IP. That is measured through the real stack in docs/smoke-test.md, section "A replaced service".
 */
class UpstreamClientConfigTest {

    @Test
    void theUpstreamClientGetsABoundedResolver() {
        HttpClient plain = HttpClient.create();
        HttpClient customized = new UpstreamClientConfig().boundedDnsCache(Duration.ofSeconds(10)).customize(plain);

        assertThat(customized.configuration().resolver()).isNotSameAs(plain.configuration().resolver());
    }
}
