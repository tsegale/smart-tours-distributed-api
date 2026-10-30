package com.smarttours.core.support;

import org.junit.jupiter.api.BeforeEach;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.context.ImportTestcontainers;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * Base for tests against real infrastructure: the real Flyway migrations on real PostGIS, a real
 * RabbitMQ (listeners, retry and dead-lettering run as in production) and a real Redis, all singletons
 * shared across test classes (see {@link SharedContainers}). Skipped automatically when Docker isn't
 * available; always runs in CI.
 */
@SpringBootTest(properties = "smarttours.messaging.retry-delay=1s")
@ImportTestcontainers(SharedContainers.class)
@Testcontainers(disabledWithoutDocker = true)
public abstract class IntegrationTest {

    protected static final GenericContainer<?> REDIS = SharedContainers.redis();

    static {
        REDIS.start();
    }

    @DynamicPropertySource
    static void sharedRedis(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", REDIS::getHost);
        registry.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    }

    /** Every test starts with an empty cache, so no test sees another's cached search results. */
    @BeforeEach
    void flushSharedRedis() throws Exception {
        REDIS.execInContainer("redis-cli", "FLUSHALL");
    }

    /** The real template, spied, so tests can assert what was published. */
    @MockitoSpyBean
    protected RabbitTemplate rabbitTemplate;
}
