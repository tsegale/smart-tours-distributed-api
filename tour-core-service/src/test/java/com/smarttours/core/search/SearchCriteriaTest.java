package com.smarttours.core.search;

import com.smarttours.core.tour.Category;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SearchCriteriaTest {

    private static final ZoneId WINDHOEK = ZoneId.of("Africa/Windhoek");

    @Test
    void equivalentSearches_haveTheSameCanonicalForm() {
        SearchCriteria a = SearchCriteria.from(params("  sunrise   dunes ", new BigDecimal("1850"),
                List.of(Category.SAFARI, Category.DESERT, Category.SAFARI)), WINDHOEK);
        SearchCriteria b = SearchCriteria.from(params("Sunrise Dunes", new BigDecimal("1850.00"),
                List.of(Category.DESERT, Category.SAFARI)), WINDHOEK);

        assertThat(a.canonical()).isEqualTo(b.canonical());
        assertThat(a.filterFingerprint()).isEqualTo(b.filterFingerprint());
        assertThat(a.canonical()).isEqualTo("q=sunrise dunes;maxPrice=1850;category=SAFARI,DESERT;sort=relevance;size=20");
    }

    @Test
    void differentSearches_differ() {
        SearchCriteria a = SearchCriteria.from(params("dunes", null, null), WINDHOEK);
        SearchCriteria b = SearchCriteria.from(params("dune", null, null), WINDHOEK);

        assertThat(a.filterFingerprint()).isNotEqualTo(b.filterFingerprint());
    }

    @Test
    void dates_areWholeDaysInTheConfiguredZone_toInclusive() {
        SearchCriteria c = SearchCriteria.from(new SearchParams(null, null, null, null, null, null, null, null, null,
                null, null, null, LocalDate.of(2030, 1, 10), LocalDate.of(2030, 1, 12), null, null, null, null),
                WINDHOEK);

        assertThat(c.departures().from()).hasToString("2030-01-09T22:00:00Z");
        assertThat(c.departures().to()).hasToString("2030-01-12T22:00:00Z");
        assertThat(c.departures().minSlots()).isEqualTo(1);
    }

    @Test
    void cursor_roundTripsAndIsBoundToItsSearch() {
        SearchCriteria criteria = SearchCriteria.from(params("dunes", null, null), WINDHOEK);
        String token = SearchCursor.after(SearchSort.RELEVANCE, "0.1", 42, criteria.filterFingerprint()).encode();

        assertThat(token).matches("[A-Za-z0-9_-]+");
        SearchCursor decoded = SearchCursor.decode(token, criteria);
        assertThat(decoded.key()).isEqualTo("0.1");
        assertThat(decoded.id()).isEqualTo(42);

        SearchCriteria other = SearchCriteria.from(params("dunes", new BigDecimal("10"), null), WINDHOEK);
        assertThatThrownBy(() -> SearchCursor.decode(token, other)).isInstanceOf(InvalidSearchException.class);
    }

    private static SearchParams params(String q, BigDecimal maxPrice, List<Category> categories) {
        return new SearchParams(null, null, null, null, q, null, maxPrice, categories, null, null, null, null,
                null, null, null, null, null, null);
    }
}
