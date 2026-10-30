package com.smarttours.core.tour;

import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourRequest;
import com.smarttours.core.tour.dto.TourResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.util.UriComponentsBuilder;

import java.time.Duration;
import java.util.List;

/**
 * Tour inventory API. Authentication and the OPERATOR role for writes are enforced by
 * the API gateway; this service is only reachable inside the compose network.
 * Parameter constraints use Spring's built-in method validation (400 + Problem Details).
 */
@RestController
@RequestMapping("/api/tours")
public class TourController {

    /** Shared caches may keep a tour for a minute; If-None-Match revalidates it for free after that. */
    static final CacheControl TOUR_CACHE_CONTROL = CacheControl.maxAge(Duration.ofSeconds(60)).cachePublic();

    private final TourService tourService;

    public TourController(TourService tourService) {
        this.tourService = tourService;
    }

    @PostMapping
    public ResponseEntity<TourResponse> create(@Valid @RequestBody TourRequest request,
                                               UriComponentsBuilder uri) {
        VersionedTour created = tourService.create(request);
        return ResponseEntity
                .created(uri.path("/api/tours/{id}").buildAndExpand(created.tour().id()).toUri())
                .eTag(created.etag())
                .body(created.tour());
    }

    @GetMapping
    public PageResponse<TourResponse> list(@RequestParam(defaultValue = "0") @Min(0) int page,
                                           @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
        return tourService.list(page, size);
    }

    /**
     * The tour with its version as a strong ETag. A matching If-None-Match gets 304 with no body:
     * Spring compares it, because the ETag is set on a 200 ResponseEntity for a GET.
     */
    @GetMapping("/{id}")
    public ResponseEntity<TourResponse> get(@PathVariable long id) {
        VersionedTour found = tourService.get(id);
        return ResponseEntity.ok()
                .eTag(found.etag())
                .cacheControl(TOUR_CACHE_CONTROL)
                .body(found.tour());
    }

    /**
     * Replaces a tour. If-Match must carry the ETag from a GET: 428 without it, 412 when the tour
     * has changed since (another operator's edit, or a new rating).
     */
    @PutMapping("/{id}")
    public ResponseEntity<TourResponse> update(@PathVariable long id,
                                               @Valid @RequestBody TourRequest request,
                                               @RequestHeader(value = HttpHeaders.IF_MATCH, required = false)
                                               String ifMatch) {
        VersionedTour updated = tourService.update(id, request, ifMatch);
        return ResponseEntity.ok().eTag(updated.etag()).body(updated.tour());
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
