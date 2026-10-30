package com.smarttours.core.support;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * Base for tests against real infrastructure: the real Flyway migrations on real PostGIS.
 *
 * <p>The container is a singleton, started once per JVM and shared by every subclass (and by the
 * cached Spring context they share), instead of one container per test class. Skipped
 * automatically when Docker isn't available; always runs in CI.
 */
@SpringBootTest
@Testcontainers(disabledWithoutDocker = true)
public abstract class IntegrationTest {

    @ServiceConnection
    protected static final PostgreSQLContainer POSTGIS = new PostgreSQLContainer(
            DockerImageName.parse("postgis/postgis:16-3.4").asCompatibleSubstituteFor("postgres"));

    static {
        POSTGIS.start();
    }

    @MockitoBean
    protected RabbitTemplate rabbitTemplate; // publishing is asserted on the mock; no broker needed here
}
