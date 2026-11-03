package com.smarttours.core.ops;

import com.smarttours.core.support.IntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.boot.test.context.SpringBootTest;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * A real server on a random port, so the warm-up runs as in production: it sends its requests while the service
 * still refuses traffic, and the service goes ready afterwards even though every provider is unreachable here
 * (the weather requests fail, and are only logged).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"smarttours.messaging.retry-delay=1s", "smarttours.warm-up.timeout=60s"})
class WarmUpIntegrationTest extends IntegrationTest {

    @Autowired
    private WarmUpRunner warmUp;

    @Autowired
    private ApplicationAvailability availability;

    @Test
    void warmsUpWhileRefusingTrafficThenGoesReady() {
        WarmUpRunner.Result result = warmUp.lastResult();

        assertThat(result).isNotNull();
        assertThat(result.readinessDuringWarmUp()).isEqualTo(ReadinessState.REFUSING_TRAFFIC);
        // The list and both searches, three rounds, plus four requests per sample tour if any exist.
        assertThat(result.requests()).isGreaterThanOrEqualTo(9);
        assertThat(availability.getReadinessState()).isEqualTo(ReadinessState.ACCEPTING_TRAFFIC);
    }
}
