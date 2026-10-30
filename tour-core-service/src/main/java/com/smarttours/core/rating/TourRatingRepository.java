package com.smarttours.core.rating;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import java.util.UUID;

/** Adds verified ratings to tours, exactly once per event. */
@Repository
public class TourRatingRepository {

    /**
     * Dedupe and update in one statement, so one transaction: the event id is recorded, and only if
     * that insert happened (first delivery) does the tour get the rating. A redelivery conflicts on
     * the primary key, inserts nothing and so updates nothing. Two deliveries racing serialise on the
     * key's index entry. The version bump makes the change visible to ETags and to If-Match.
     */
    private static final String ADD_RATING_ONCE = """
            WITH first_delivery AS (
                INSERT INTO processed_events (event_id, event_type)
                VALUES (:eventId, 'review.submitted')
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            )
            UPDATE tours
               SET rating_sum = rating_sum + :rating,
                   rating_count = rating_count + 1,
                   version = version + 1,
                   updated_at = now()
             WHERE id = :tourId
               AND EXISTS (SELECT 1 FROM first_delivery)
            """;

    private final JdbcClient jdbc;

    public TourRatingRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** True if the rating was added now; false for a duplicate delivery or an unknown tour. */
    public boolean addRatingOnce(UUID eventId, long tourId, int rating) {
        return jdbc.sql(ADD_RATING_ONCE)
                .param("eventId", eventId)
                .param("tourId", tourId)
                .param("rating", rating)
                .update() == 1;
    }
}
