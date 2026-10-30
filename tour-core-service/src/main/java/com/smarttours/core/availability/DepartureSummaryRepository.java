package com.smarttours.core.availability;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** Writes the departure read model. Owned by tour-core; fed only by availability.changed events. */
@Repository
public class DepartureSummaryRepository {

    /**
     * One statement, safe against duplicates and reordering: the row is inserted, or overwritten
     * only when the event's version is newer than the one stored. A redelivered or overtaken event
     * changes nothing, so no processed-events bookkeeping is needed here.
     */
    private static final String UPSERT_IF_NEWER = """
            INSERT INTO tour_departure_summary
                (availability_id, tour_id, starts_at, total_slots, slots_left, source_version, updated_at)
            VALUES (:availabilityId, :tourId, :startsAt, :totalSlots, :slotsLeft, :version, now())
            ON CONFLICT (availability_id) DO UPDATE
               SET tour_id = EXCLUDED.tour_id,
                   starts_at = EXCLUDED.starts_at,
                   total_slots = EXCLUDED.total_slots,
                   slots_left = EXCLUDED.slots_left,
                   source_version = EXCLUDED.source_version,
                   updated_at = now()
             WHERE tour_departure_summary.source_version < EXCLUDED.source_version
            """;

    private final JdbcClient jdbc;

    public DepartureSummaryRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** True if the event was applied; false if the stored version was already as new or newer. */
    public boolean applyIfNewer(AvailabilityChangedEvent event) {
        return jdbc.sql(UPSERT_IF_NEWER)
                .param("availabilityId", event.availabilityId())
                .param("tourId", event.tourId())
                .param("startsAt", event.startsAt())
                .param("totalSlots", event.totalSlots())
                .param("slotsLeft", event.slotsLeft())
                .param("version", event.version())
                .update() == 1;
    }
}
