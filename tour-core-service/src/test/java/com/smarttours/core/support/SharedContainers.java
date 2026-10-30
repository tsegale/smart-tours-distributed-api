package com.smarttours.core.support;

import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.rabbitmq.RabbitMQContainer;
import org.testcontainers.utility.DockerImageName;

import java.time.Duration;

/**
 * PostGIS and RabbitMQ, started once per JVM and shared by every integration test (and by the cached
 * Spring contexts). Imported with {@code @ImportTestcontainers}, so their connection details come from
 * {@code @ServiceConnection}. Redis is wired separately, so a test can bring a Redis of its own.
 */
public final class SharedContainers {

    /** RabbitMQ alone can take over a minute to boot on a loaded laptop or a shared CI runner. */
    private static final Duration STARTUP_TIMEOUT = Duration.ofMinutes(3);

    @ServiceConnection
    public static final PostgreSQLContainer POSTGIS = new PostgreSQLContainer(
            DockerImageName.parse("postgis/postgis:16-3.4").asCompatibleSubstituteFor("postgres"))
            .withStartupTimeout(STARTUP_TIMEOUT);

    @ServiceConnection
    public static final RabbitMQContainer RABBITMQ = new RabbitMQContainer(
            DockerImageName.parse("rabbitmq:4.1-management-alpine").asCompatibleSubstituteFor("rabbitmq"))
            .withStartupTimeout(STARTUP_TIMEOUT);

    static {
        POSTGIS.start();
        RABBITMQ.start();
    }

    private SharedContainers() {
    }

    public static GenericContainer<?> redis() {
        return new GenericContainer<>(DockerImageName.parse("redis:7.4-alpine")).withExposedPorts(6379);
    }
}
