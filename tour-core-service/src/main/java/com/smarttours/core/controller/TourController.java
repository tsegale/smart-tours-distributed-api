package main.java.com.smarttours.core.controller;


import org.springframework.amqp.rabbit.core.RabbitTemplate;
import com.smarttours.core.config.RabbitMQConfig;
import com.fasterxml.jackson.databind.ObjectMapper;
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

    @Autowired
    private RabbitTemplate rabbitTemplate; // Injects the RabbitMQ client

    private final ObjectMapper objectMapper = new ObjectMapper();

    // Standard SRID for GPS coordinates (WGS 84)
    private final GeometryFactory geometryFactory = new GeometryFactory(new PrecisionModel(), 4326);

    @PostMapping
    public Tour createTour(@RequestBody TourRequest request) {
        Tour tour = new Tour();
        tour.setName(request.getName());
        tour.setPrice(request.getPrice());

        Tour savedTour = tourRepository.save(tour);

        // Publish the event asynchronously
        try {
            // Create a JSON string with the data the AI service needs
            String payload = String.format("{\"tour_id\":\"%s\", \"title\":\"%s\", \"description\":\"%s\"}",
                    savedTour.getId(), savedTour.getName(), request.getDescription());
            
            rabbitTemplate.convertAndSend(RabbitMQConfig.EXCHANGE_NAME, RabbitMQConfig.ROUTING_KEY_CREATED, payload);
            System.out.println("Published tour creation event to RabbitMQ");
        } catch (Exception e) {
            // Log error, but DO NOT fail the request. The user's tour was still saved successfully.
            System.err.println("Failed to publish event: " + e.getMessage());
        }
        
        // Convert plain latitude/longitude into a mathematical Spatial Point
        Point point = geometryFactory.createPoint(new Coordinate(request.getLongitude(), request.getLatitude()));
        tour.setLocation(point);
        
        return savedTour;
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
    // Getters and setters omitted for brevity
    public String getName() { return name; }
    public Double getPrice() { return price; }
    public double getLatitude() { return latitude; }
    public double getLongitude() { return longitude; }
}
