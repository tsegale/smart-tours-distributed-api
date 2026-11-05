package com.smarttours.core.common;

import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.support.SharedContainers;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * With the database frozen, search answers quickly with a retryable 503. A fire drill found every search hanging past its
 * client's 15 s timeout on Hikari's default 30 s wait. The shared container is paused and always resumed.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "spring.datasource.hikari.max-lifetime=60000")
class DatabaseOutageIntegrationTest extends IntegrationTest {

    @Value("${local.server.port}")
    private int port;

    private HttpResponse<String> search() throws Exception {
        URI uri = URI.create("http://localhost:" + port + "/api/tours/search?size=1&q=" + UUID.randomUUID());
        return HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(14)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
    }

    @Test
    void searchAnswers503QuicklyWhileTheDatabaseIsDown() throws Exception {
        assertThat(search().statusCode()).isEqualTo(200);
        Thread.sleep(600); // Hikari validates a connection idle for over 500 ms before handing it out

        var docker = SharedContainers.POSTGIS.getDockerClient();
        String container = SharedContainers.POSTGIS.getContainerId();
        docker.pauseContainerCmd(container).exec();
        try {
            long started = System.nanoTime();
            HttpResponse<String> response = search();
            long elapsedMs = Duration.ofNanos(System.nanoTime() - started).toMillis();

            assertThat(response.statusCode()).isEqualTo(503);
            assertThat(response.headers().firstValue(HttpHeaders.RETRY_AFTER)).contains("5");
            assertThat(response.headers().firstValue(HttpHeaders.CONTENT_TYPE).orElse("")).contains("problem+json");
            assertThat(response.body()).contains("database-unavailable");
            assertThat(elapsedMs).as("answers well inside a client's timeout").isLessThan(8_000);
        } finally {
            docker.unpauseContainerCmd(container).exec();
        }
    }
}
