package com.smarttours.core.messaging;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * Consumer retry policy, the same as the AI worker's (ADR 0005): a failed message waits in the
 * retry queue for {@code retryDelay}, up to {@code maxRetries} times, then goes to the DLQ.
 */
@ConfigurationProperties("smarttours.messaging")
public record MessagingProperties(
        @DefaultValue("5") int maxRetries,
        @DefaultValue("10s") Duration retryDelay) {
}
