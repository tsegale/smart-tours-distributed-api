package com.smarttours.core.controller;

import com.smarttours.core.model.Tour;
import com.smarttours.core.repository.TourRepository;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/tours")
public class TourController {

    @Autowired
    private TourRepository tourRepository;

    // Standard SRID for GPS coordinates (WGS 84)
    private final GeometryFactory geometryFactory = new GeometryFactory(new PrecisionModel(), 4326);

    @PostMapping
    public Tour createTour(@RequestBody TourRequest request) {
        Tour tour = new Tour();
        tour.setName(request.getName());
        tour.setPrice(request.getPrice());
        
        // Convert plain latitude/longitude into a mathematical Spatial Point
        Point point = geometryFactory.createPoint(new Coordinate(request.getLongitude(), request.getLatitude()));
        tour.setLocation(point);
        
        return tourRepository.save(tour);
    }

    @GetMapping
    public List<Tour> getAllTours() {
        return tourRepository.findAll();
    }

    @GetMapping("/{id}")
    public org.springframework.http.ResponseEntity<Tour> getTourById(@PathVariable Long id) {
        return tourRepository.findById(id)
                .map(org.springframework.http.ResponseEntity::ok)
                .orElse(org.springframework.http.ResponseEntity.notFound().build());
    }

    @GetMapping("/nearby")
    public List<Tour> getNearbyTours(@RequestParam double lon, @RequestParam double lat, @RequestParam double radius) {
        return tourRepository.findToursWithinRadius(lon, lat, radius);
    }
}

class TourRequest {
    private String name;
    private Double price;
    private double latitude;
    private double longitude;

    public TourRequest() {}

    public TourRequest(String name, Double price, double latitude, double longitude) {
        this.name = name;
        this.price = price;
        this.latitude = latitude;
        this.longitude = longitude;
    }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }
    public double getLatitude() { return latitude; }
    public void setLatitude(double latitude) { this.latitude = latitude; }
    public double getLongitude() { return longitude; }
    public void setLongitude(double longitude) { this.longitude = longitude; }
}
