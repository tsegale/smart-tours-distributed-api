package com.smarttours.core.search;

import com.smarttours.core.fx.DisplayPricing;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import org.springdoc.core.annotations.ParameterObject;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Catalogue search. Public through the gateway (browsing needs no account), rate limited there.
 */
@RestController
public class SearchController {

    private final TourSearchService searchService;
    private final DisplayPricing displayPricing;

    public SearchController(TourSearchService searchService, DisplayPricing displayPricing) {
        this.searchService = searchService;
        this.displayPricing = displayPricing;
    }

    @Operation(summary = "Search tours",
            description = "Combine any filters: a radius or a bounding box, full text, price, category, difficulty, "
                    + "duration, rating, and departures with free seats in a date range. Departure availability "
                    + "comes from an event-fed read model and may lag booking-service by a few seconds; a booking "
                    + "is always checked against booking-service itself. Pages are keyset-paginated: pass "
                    + "nextCursor back as cursor, with the same filters. With currency, every hit gets a "
                    + "displayPrice (display only: bookings are charged in NAD) and minPrice/maxPrice are read "
                    + "in that currency.")
    @ApiResponse(responseCode = "200", description = "A page of results")
    @ApiResponse(responseCode = "400", description = "Contradictory or out-of-range parameters, or an unsupported "
            + "currency (Problem Details)")
    @ApiResponse(responseCode = "503", description = "Price bounds in a currency for which no usable rate exists")
    @GetMapping("/api/tours/search")
    public SearchResponse search(@ParameterObject SearchParams params,
                                 @Parameter(description = "Display currency: NAD, ZAR, USD, EUR, GBP or BWP")
                                 @RequestParam(required = false) String currency) {
        return searchService.search(params, displayPricing.forCurrency(currency));
    }
}
