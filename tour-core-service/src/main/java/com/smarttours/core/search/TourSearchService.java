package com.smarttours.core.search;

import com.smarttours.core.cache.CatalogueCache;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

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
        SearchCriteria criteria = SearchCriteria.from(params, zone);
        if (params.cursor() == null) {
            return cache.firstPage(criteria, () -> load(criteria, null));
        }
        return load(criteria, SearchCursor.decode(params.cursor(), criteria));
    }

    private SearchResponse load(SearchCriteria criteria, SearchCursor after) {
        TourSearchRepository.Page page = repository.search(criteria, after);
        return new SearchResponse(page.hits(), page.hits().size(), page.nextCursor());
    }
}
