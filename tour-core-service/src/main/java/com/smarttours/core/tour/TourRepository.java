package com.smarttours.core.tour;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface TourRepository extends JpaRepository<Tour, Long> {

    /**
     * Tours within {@code radiusMeters} of a point, nearest first.
     * Casting to geography makes PostGIS measure in metres on the spheroid, and the
     * expression matches the GiST index in V1__create_tours.sql, so this is an index scan.
     */
    @Query(value = """
            SELECT t.*
            FROM tours t
            WHERE ST_DWithin(
                    CAST(t.location AS geography),
                    CAST(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS geography),
                    :radiusMeters)
            ORDER BY ST_Distance(
                    CAST(t.location AS geography),
                    CAST(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS geography))
            LIMIT :maxResults
            """, nativeQuery = true)
    List<Tour> findWithinRadius(@Param("lat") double lat,
                                @Param("lon") double lon,
                                @Param("radiusMeters") double radiusMeters,
                                @Param("maxResults") int maxResults);
}
