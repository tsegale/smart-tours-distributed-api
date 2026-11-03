package com.smarttours.core.ops;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * Warm-up before readiness.
 *
 * @param enabled        off for a quick local start; tests without a web server skip it anyway
 * @param rounds         times each warm-up request runs: the first round loads classes and fills caches, the
 *                       next ones reach the paths that only run on a cache hit
 * @param requestTimeout one warm-up request, outbound weather calls included
 * @param timeout        the whole warm-up: past this the service goes ready anyway, so a slow provider can delay
 *                       readiness but never prevent it
 */
@ConfigurationProperties("smarttours.warm-up")
public record WarmUpProperties(boolean enabled, int rounds, Duration requestTimeout, Duration timeout) {
}
