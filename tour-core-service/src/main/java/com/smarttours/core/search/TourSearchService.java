package com.smarttours.core.search;

import com.smarttours.core.cache.CatalogueCache;
import com.smarttours.core.fx.CurrencyConversion;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.ZoneId;

@Service
public class TourSearchService {

    private final TourSearchRepository repository;
    private final CatalogueCache cache;
    private final ZoneId zone;

    public TourSearchService(TourSearchRepository repository, CatalogueCache cache,
                             @Value("${smarttours.search.zone:Africa/Windhoek}") ZoneId zone) {
        this.repository = repository;
        this.cache = cache;
        this.zone = zone;
    }

    public SearchResponse search(SearchParams params) {
        return search(params, CurrencyConversion.none());
    }

    /**
     * Price bounds given in the display currency are converted to NAD first, so the query, the cache key and
     * the cursor see NAD only. Display prices are added to the hits last, after the cache (ADR 0011).
     */
    public SearchResponse search(SearchParams params, CurrencyConversion conversion) {
        SearchCriteria criteria = SearchCriteria.from(inBaseCurrency(params, conversion), zone);
        SearchResponse response = params.cursor() == null
                ? cache.firstPage(criteria, () -> load(criteria, null))
                : load(criteria, SearchCursor.decode(params.cursor(), criteria));
        if (!conversion.changesRepresentation()) {
            return response;
        }
        return new SearchResponse(response.items().stream().map(hit -> hit.priced(conversion)).toList(),
                response.size(), response.nextCursor());
    }

    /** Bounds are widened to the cent (min down, max up), so a tour shown at exactly the bound is included. */
    private static SearchParams inBaseCurrency(SearchParams params, CurrencyConversion conversion) {
        if (params.minPrice() == null && params.maxPrice() == null) {
            return params;
        }
        return params.withPriceRange(toBase(params.minPrice(), conversion, RoundingMode.FLOOR),
                toBase(params.maxPrice(), conversion, RoundingMode.CEILING));
    }

    private static BigDecimal toBase(BigDecimal amount, CurrencyConversion conversion, RoundingMode rounding) {
        return amount == null ? null : conversion.toBase(amount, rounding);
    }

    private SearchResponse load(SearchCriteria criteria, SearchCursor after) {
        TourSearchRepository.Page page = repository.search(criteria, after);
        return new SearchResponse(page.hits(), page.hits().size(), page.nextCursor());
    }
}
