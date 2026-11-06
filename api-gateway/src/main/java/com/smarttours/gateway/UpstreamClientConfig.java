package com.smarttours.gateway;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.config.HttpClientCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

/**
 * Bounds how long the gateway trusts a service's address. By default it keeps a record for its DNS TTL, and Docker's DNS
 * answers with a long one, so a replaced container with a new IP stayed unreachable for over five minutes after it was
 * healthy, measured: every request went to the old address and answered 502 or 504.
 *
 * The cost is one lookup per service per interval, against a resolver on the same network.
 */
@Configuration(proxyBeanMethods = false)
class UpstreamClientConfig {

    @Bean
    HttpClientCustomizer boundedDnsCache(@Value("${smarttours.upstream.dns-max-ttl:10s}") Duration maxTtl) {
        return client -> client.resolver(spec -> spec.cacheMaxTimeToLive(maxTtl));
    }
}
