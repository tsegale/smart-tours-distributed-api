package com.smarttours.core.outbox;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * The outbox relay (ADR 0013). Same defaults as booking-service's relay (ADR 0008).
 *
 * @param pollInterval   pause after a batch that wasn't full; a full batch is followed by the next at once
 * @param batchSize      rows claimed, published and marked per transaction
 * @param confirmTimeout how long to wait for the broker to confirm a batch before giving up on it
 * @param retention      published rows are deleted after this
 * @param pruneInterval  how often to delete them
 * @param pruneBatchSize rows deleted per statement
 */
@ConfigurationProperties("smarttours.outbox")
public record OutboxProperties(
        Duration pollInterval,
        int batchSize,
        Duration confirmTimeout,
        Duration retention,
        Duration pruneInterval,
        int pruneBatchSize) {
}
