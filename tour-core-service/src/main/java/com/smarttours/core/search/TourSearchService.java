package com.smarttours.core.search;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.ZoneId;

@Service
public class TourSearchService {

    private final TourSearchRepository repository;
    private final ZoneId zone;

    public TourSearchService(TourSearchRepository repository,
                             @Value("${smarttours.search.zone:Africa/Windhoek}") ZoneId zone) {
        this.repository = repository;
        this.zone = zone;
    }

    public SearchResponse search(SearchParams params) {
        SearchCriteria criteria = SearchCriteria.from(params, zone);
        return load(criteria, params.cursor() == null ? null : SearchCursor.decode(params.cursor(), criteria));
    }

    private SearchResponse load(SearchCriteria criteria, SearchCursor after) {
        TourSearchRepository.Page page = repository.search(criteria, after);
        return new SearchResponse(page.hits(), page.hits().size(), page.nextCursor());
    }
}
