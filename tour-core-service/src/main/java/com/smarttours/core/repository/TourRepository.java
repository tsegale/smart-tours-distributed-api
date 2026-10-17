package com.smarttours.core.repository;

import com.smarttours.core.model.Tour;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.List;

public interface TourRepository extends JpaRepository<Tour, Long> {
    
    // ST_DWithin checks if two geometries are within a given distance (in meters for SRID 4326 geography cast)
    @Query(value = "SELECT * FROM tours t WHERE ST_DWithin(t.location\\:\\:geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)\\:\\:geography, :radiusMeters)", nativeQuery = true)
    List<Tour> findToursWithinRadius(@Param("lon") double lon, @Param("lat") double lat, @Param("radiusMeters") double radiusMeters);
}
