package com.smarttours.core.tour;

import com.smarttours.core.tour.dto.CreateTourRequest;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.util.UriComponentsBuilder;

import java.util.List;

/**
 * Tour inventory API. Authentication and the OPERATOR role for writes are enforced by
 * the API gateway; this service is only reachable inside the compose network.
 * Parameter constraints use Spring's built-in method validation (400 + Problem Details).
 */
@RestController
@RequestMapping("/api/tours")
public class TourController {

    private final TourService tourService;

    public TourController(TourService tourService) {
        this.tourService = tourService;
    }

    @PostMapping
    public ResponseEntity<TourResponse> create(@Valid @RequestBody CreateTourRequest request,
                                               UriComponentsBuilder uri) {
        TourResponse created = tourService.create(request);
        return ResponseEntity
                .created(uri.path("/api/tours/{id}").buildAndExpand(created.id()).toUri())
                .body(created);
    }

    @GetMapping
    public PageResponse<TourResponse> list(@RequestParam(defaultValue = "0") @Min(0) int page,
                                           @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return tourService.list(page, size);
    }

    @GetMapping("/{id}")
    public TourResponse get(@PathVariable long id) {
        return tourService.get(id);
    }

    /**
     * Tours within {@code radius} metres of ({@code lat}, {@code lon}), nearest first.
     * Radius is capped at 500 km, which covers most of Namibia from any town.
     */
    @GetMapping("/nearby")
    public List<TourResponse> nearby(@RequestParam @DecimalMin("-90.0") @DecimalMax("90.0") double lat,
                                     @RequestParam @DecimalMin("-180.0") @DecimalMax("180.0") double lon,
                                     @RequestParam @Positive @DecimalMax("500000") double radius) {
        return tourService.nearby(lat, lon, radius);
    }
}
