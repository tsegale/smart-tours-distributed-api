package com.smarttours.core.support;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.context.ImportTestcontainers;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Base for tests against real infrastructure: the real Flyway migrations on real PostGIS and a real
 * RabbitMQ (listeners, retry and dead-lettering run as in production), singletons shared across test
 * classes (see {@link SharedContainers}). Skipped automatically when Docker isn't available; always
 * runs in CI.
 */
@SpringBootTest(properties = "smarttours.messaging.retry-delay=1s")
@ImportTestcontainers(SharedContainers.class)
@Testcontainers(disabledWithoutDocker = true)
public abstract class IntegrationTest {

    /** The real template, spied, so tests can assert what was published. */
    @MockitoSpyBean
    protected RabbitTemplate rabbitTemplate;
}
