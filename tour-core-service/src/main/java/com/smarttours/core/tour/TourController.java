package com.smarttours.core.tour;

import com.smarttours.core.common.InvalidParameterException;
import com.smarttours.core.fx.CurrencyConversion;
import com.smarttours.core.fx.DisplayPricing;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourDetailResponse;
import com.smarttours.core.tour.dto.TourRequest;
import com.smarttours.core.tour.dto.TourResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import com.smarttours.core.weather.DepartureWeather;
import io.swagger.v3.oas.annotations.Parameter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import org.springframework.format.annotation.DateTimeFormat;
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
import java.time.LocalDate;
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

    static final String CURRENCY_DOC = "Display currency (NAD, ZAR, USD, EUR, GBP, BWP): adds displayPrice. "
            + "Display only: bookings are charged in NAD.";

    static final String INCLUDE_WEATHER = "weather";

    private final TourService tourService;
    private final DisplayPricing displayPricing;
    private final DepartureWeather departureWeather;

    public TourController(TourService tourService, DisplayPricing displayPricing, DepartureWeather departureWeather) {
        this.tourService = tourService;
        this.displayPricing = displayPricing;
        this.departureWeather = departureWeather;
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
                                           @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size,
                                           @Parameter(description = CURRENCY_DOC)
                                           @RequestParam(required = false) String currency) {
        CurrencyConversion conversion = displayPricing.forCurrency(currency);
        PageResponse<TourResponse> tours = tourService.list(page, size);
        if (!conversion.changesRepresentation()) {
            return tours;
        }
        return new PageResponse<>(tours.content().stream().map(tour -> tour.priced(conversion)).toList(),
                tours.page(), tours.size(), tours.totalElements(), tours.totalPages());
    }

    /**
     * The tour with its version as a strong ETag. A matching If-None-Match gets 304 with no body:
     * Spring compares it, because the ETag is set on a 200 ResponseEntity for a GET.
     *
     * <p>With {@code currency} or {@code include=weather} the body also depends on exchange rates or weather,
     * which the version doesn't capture, so those responses carry no ETag (a 304 would serve an old rate or
     * forecast). The plain tour, which the If-Match of a PUT refers to, keeps it.
     */
    @GetMapping("/{id}")
    public ResponseEntity<?> get(@PathVariable long id,
                                 @Parameter(description = CURRENCY_DOC)
                                 @RequestParam(required = false) String currency,
                                 @Parameter(description = "weather: embed the weather for the departure date")
                                 @RequestParam(required = false) String include,
                                 @Parameter(description = "With include=weather: the departure date (default: the "
                                         + "tour's next departure with seats)")
                                 @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE)
                                 LocalDate date) {
        boolean withWeather = includesWeather(include);
        CurrencyConversion conversion = displayPricing.forCurrency(currency);
        VersionedTour found = tourService.get(id);
        if (!withWeather && !conversion.changesRepresentation()) {
            return ResponseEntity.ok()
                    .eTag(found.etag())
                    .cacheControl(TOUR_CACHE_CONTROL)
                    .body(found.tour());
        }
        TourResponse tour = found.tour().priced(conversion);
        Object body = withWeather
                ? TourDetailResponse.of(tour, departureWeather.forTour(id, tour.latitude(), tour.longitude(), date))
                : tour;
        return ResponseEntity.ok().cacheControl(TOUR_CACHE_CONTROL).body(body);
    }

    private static boolean includesWeather(String include) {
        if (include == null || include.isBlank()) {
            return false;
        }
        if (!INCLUDE_WEATHER.equalsIgnoreCase(include.trim())) {
            throw new InvalidParameterException("include", "include supports only '" + INCLUDE_WEATHER + "'");
        }
        return true;
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
