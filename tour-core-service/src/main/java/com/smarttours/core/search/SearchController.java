package com.smarttours.core.search;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import org.springdoc.core.annotations.ParameterObject;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Catalogue search. Public through the gateway (browsing needs no account), rate limited there.
 */
@RestController
public class SearchController {

    private final TourSearchService searchService;

    public SearchController(TourSearchService searchService) {
        this.searchService = searchService;
    }

    @Operation(summary = "Search tours",
            description = "Combine any filters: a radius or a bounding box, full text, price, category, difficulty, "
                    + "duration, rating, and departures with free seats in a date range. Departure availability "
                    + "comes from an event-fed read model and may lag booking-service by a few seconds; a booking "
                    + "is always checked against booking-service itself. Pages are keyset-paginated: pass "
                    + "nextCursor back as cursor, with the same filters.")
    @ApiResponse(responseCode = "200", description = "A page of results")
    @ApiResponse(responseCode = "400", description = "Contradictory or out-of-range parameters (Problem Details)")
    @GetMapping("/api/tours/search")
    public SearchResponse search(@ParameterObject SearchParams params) {
        return searchService.search(params);
    }
}
