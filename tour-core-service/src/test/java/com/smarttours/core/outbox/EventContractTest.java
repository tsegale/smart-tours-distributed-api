package com.smarttours.core.outbox;

import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SchemaRegistryConfig;
import com.networknt.schema.SpecificationVersion;
import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.events.TourUpdatedEvent;
import com.smarttours.core.support.IntegrationTest;
import com.smarttours.core.support.TourFixtures;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Contract: every event tour-core writes to its outbox matches its schema in docs/events, the source of truth for
 * every event in the system (docs/events.md). The schemas are read from the repository, not copied into the tests.
 */
@AutoConfigureMockMvc
class EventContractTest extends IntegrationTest {

    private static final Path SCHEMA = Path.of("..", "docs", "events", "tour-event.schema.json");

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private JsonMapper json;

    private Schema schema;

    @BeforeEach
    void loadSchemaAndEmptyTheOutbox() throws IOException {
        SchemaRegistryConfig config = SchemaRegistryConfig.builder().formatAssertionsEnabled(true).build();
        SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12,
                builder -> builder.schemaRegistryConfig(config));
        try (InputStream in = Files.newInputStream(SCHEMA)) {
            schema = registry.getSchema(in);
        }
        jdbc.sql("DELETE FROM outbox").update();
    }

    @Test
    void theSchemaDocumentsEveryEventTypeTourCoreProduces() throws IOException {
        JsonNode documented = json.readTree(Files.readString(SCHEMA)).path("properties").path("eventType").path("enum");
        Set<String> types = documented.valueStream().map(JsonNode::asString).collect(Collectors.toSet());

        assertThat(types).containsExactlyInAnyOrder(TourCreatedEvent.TYPE, TourUpdatedEvent.TYPE);
    }

    @Test
    void createdAndUpdatedEventsMatchTheSchema() throws Exception {
        MvcResult created = mockMvc.perform(post("/api/tours").contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY))
                .andExpect(status().isCreated())
                .andReturn();
        mockMvc.perform(put(created.getResponse().getHeader(HttpHeaders.LOCATION))
                        .header(HttpHeaders.IF_MATCH, created.getResponse().getHeader(HttpHeaders.ETAG))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(TourFixtures.VALID_BODY.replace("\"durationMinutes\": 300",
                                "\"durationMinutes\": 300, \"meetingPoint\": \"Sesriem gate\", "
                                        + "\"timeZone\": \"Africa/Windhoek\"")))
                .andExpect(status().isOk());

        List<String> payloads = jdbc.sql("SELECT payload::text FROM outbox ORDER BY occurred_at")
                .query(String.class).list();

        assertThat(payloads).hasSize(2);
        for (String payload : payloads) {
            List<Error> errors = schema.validate(json.readTree(payload));
            assertThat(errors).as(payload).isEmpty();
        }
    }

    @Test
    void theSchemaCatchesDrift() {
        String good = """
                {"eventId": "0b0b7a3e-4e4f-4f6a-9d63-2a4d3f0e9c11", "eventType": "tour.created",
                 "occurredAt": "2026-11-03T10:00:00Z", "tourId": 1, "name": "Etosha", "description": null,
                 "meetingPoint": null, "timeZone": "Africa/Windhoek"}
                """;
        assertThat(schema.validate(json.readTree(good))).isEmpty();
        assertThat(schema.validate(json.readTree(good.replace("\"tourId\": 1,", "")))).as("missing").isNotEmpty();
        assertThat(schema.validate(json.readTree(good.replace("\"tourId\": 1,", "\"tourId\": 1, \"extra\": 1,"))))
                .as("undocumented field").isNotEmpty();
        assertThat(schema.validate(json.readTree(good.replace("2026-11-03T10:00:00Z", "yesterday"))))
                .as("not a date-time").isNotEmpty();
    }
}
