package com.smarttours.core.search;

import com.smarttours.core.jooq.tables.TourDepartureSummary;
import com.smarttours.core.jooq.tables.Tours;
import com.smarttours.core.search.SearchCriteria.DepartureWindow;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import org.jooq.Condition;
import org.jooq.DSLContext;
import org.jooq.Field;
import org.jooq.Record;
import org.jooq.SelectFieldOrAsterisk;
import org.jooq.SortField;
import org.jooq.impl.DSL;
import org.jooq.impl.SQLDataType;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

import static com.smarttours.core.jooq.Tables.TOURS;
import static com.smarttours.core.jooq.Tables.TOUR_DEPARTURE_SUMMARY;

/**
 * Dynamic catalogue search with jOOQ (ADR 0007): only the filters a request uses end up in the SQL,
 * every value is a bind parameter, and pages are found by keyset, not OFFSET.
 *
 * <p>Tables and columns come from code generated off the real Flyway schema. jOOQ's open-source
 * edition has no PostGIS or tsvector types (the geometry and tsvector columns are generated as
 * {@code Object}), so the spatial and full-text expressions below are jOOQ plain-SQL templates:
 * fixed SQL text with {@code {n}} placeholders that jOOQ fills with typed fields and bind values.
 * No request value is ever concatenated into SQL.
 */
@Repository
public class TourSearchRepository {

    private static final Tours T = TOURS.as("t");
    private static final TourDepartureSummary S = TOUR_DEPARTURE_SUMMARY.as("s");

    /** Same expression as the GiST index idx_tours_location_geog, so the planner can use it. */
    private static final Field<Object> LOCATION_GEOG = DSL.field("CAST({0} AS geography)", Object.class, T.LOCATION);
    private static final Field<Double> LATITUDE = DSL.field("ST_Y({0})", SQLDataType.DOUBLE, T.LOCATION);
    private static final Field<Double> LONGITUDE = DSL.field("ST_X({0})", SQLDataType.DOUBLE, T.LOCATION);

    private final DSLContext dsl;

    public TourSearchRepository(DSLContext dsl) {
        this.dsl = dsl;
    }

    public record Page(List<TourSearchHit> hits, String nextCursor) {
    }

    public Page search(SearchCriteria criteria, SearchCursor after) {
        Expressions x = new Expressions(criteria);
        SortKey<?> sortKey = x.sortKey();

        List<Condition> where = filters(criteria, x);
        if (after != null) {
            where.add(sortKey.after(after));
        }

        List<SelectFieldOrAsterisk> select = new ArrayList<>(List.of(T.ID, T.NAME, T.DESCRIPTION, T.PRICE,
                T.CURRENCY, T.CATEGORY, T.DIFFICULTY, T.DURATION_MINUTES, T.AVG_RATING, T.RATING_COUNT, T.CREATED_AT,
                LATITUDE.as("latitude"), LONGITUDE.as("longitude"), sortKey.field().as("sort_key")));
        if (x.distance != null) {
            select.add(x.distance.as("distance_meters"));
        }

        List<Record> rows = dsl.select(select)
                .from(T)
                .where(where)
                .orderBy(sortKey.orderBy())
                .limit(criteria.size() + 1) // one extra row says whether there is a next page
                .fetch();

        boolean more = rows.size() > criteria.size();
        List<Record> page = more ? rows.subList(0, criteria.size()) : rows;
        String next = null;
        if (more) {
            Record last = page.getLast();
            next = SearchCursor.after(criteria.sort(), sortKey.format(last.get("sort_key")), last.get(T.ID),
                    criteria.filterFingerprint()).encode();
        }
        return new Page(page.stream().map(r -> hit(r, x.distance != null)).toList(), next);
    }

    private static List<Condition> filters(SearchCriteria c, Expressions x) {
        List<Condition> where = new ArrayList<>();
        if (c.radiusMeters() != null) {
            // Spherical, like the <-> distance below, so every hit's distanceMeters is within the radius.
            where.add(DSL.condition("ST_DWithin({0}, {1}, {2}, false)", LOCATION_GEOG, x.point,
                    DSL.val(c.radiusMeters())));
        }
        if (c.bbox() != null) {
            var b = c.bbox();
            where.add(DSL.condition("{0} && ST_MakeEnvelope({1}, {2}, {3}, {4}, 4326)", T.LOCATION,
                    DSL.val(b.minLon()), DSL.val(b.minLat()), DSL.val(b.maxLon()), DSL.val(b.maxLat())));
        }
        if (x.tsQuery != null) {
            where.add(DSL.condition("{0} @@ {1}", T.SEARCH_VECTOR, x.tsQuery));
        }
        if (c.minPrice() != null) {
            where.add(T.PRICE.ge(c.minPrice()));
        }
        if (c.maxPrice() != null) {
            where.add(T.PRICE.le(c.maxPrice()));
        }
        if (!c.categories().isEmpty()) {
            where.add(T.CATEGORY.in(c.categories().stream().map(Category::name).toList()));
        }
        if (!c.difficulties().isEmpty()) {
            where.add(T.DIFFICULTY.in(c.difficulties().stream().map(Difficulty::name).toList()));
        }
        if (c.minDuration() != null) {
            where.add(T.DURATION_MINUTES.ge(c.minDuration()));
        }
        if (c.maxDuration() != null) {
            where.add(T.DURATION_MINUTES.le(c.maxDuration()));
        }
        if (c.minRating() != null) {
            where.add(T.AVG_RATING.ge(c.minRating()));
        }
        if (c.departures() != null) {
            where.add(DSL.exists(DSL.selectOne().from(S).where(departureConditions(c.departures()))));
        }
        return where;
    }

    /** A departure of this tour, not yet started, inside the window, with enough seats left (read model). */
    private static List<Condition> departureConditions(DepartureWindow window) {
        List<Condition> conditions = new ArrayList<>(List.of(
                S.TOUR_ID.eq(T.ID),
                S.STARTS_AT.gt(DSL.currentOffsetDateTime()),
                S.SLOTS_LEFT.ge(window.minSlots())));
        if (window.from() != null) {
            conditions.add(S.STARTS_AT.ge(window.from().atOffset(ZoneOffset.UTC)));
        }
        if (window.to() != null) {
            conditions.add(S.STARTS_AT.lt(window.to().atOffset(ZoneOffset.UTC)));
        }
        return conditions;
    }

    private static TourSearchHit hit(Record r, boolean geo) {
        int ratingCount = r.get(T.RATING_COUNT);
        Double distance = geo ? r.get("distance_meters", Double.class) : null;
        return new TourSearchHit(
                r.get(T.ID),
                r.get(T.NAME),
                r.get(T.DESCRIPTION),
                r.get(T.PRICE),
                r.get(T.CURRENCY),
                r.get("latitude", Double.class),
                r.get("longitude", Double.class),
                Category.valueOf(r.get(T.CATEGORY)),
                Difficulty.valueOf(r.get(T.DIFFICULTY)),
                r.get(T.DURATION_MINUTES),
                ratingCount == 0 ? null : r.get(T.AVG_RATING),
                ratingCount,
                r.get(T.CREATED_AT).toInstant(),
                distance == null ? null : Math.round(distance * 10) / 10.0);
    }

    /** The per-request expressions: the search point, the full-text query and what derives from them. */
    private static final class Expressions {

        final SearchCriteria criteria;
        final Field<Object> point;
        final Field<Double> distance;
        final Field<Object> tsQuery;
        final Field<Float> rank;

        Expressions(SearchCriteria c) {
            criteria = c;
            if (c.hasPoint()) {
                point = DSL.field("CAST(ST_SetSRID(ST_MakePoint({0}, {1}), 4326) AS geography)", Object.class,
                        DSL.val(c.point().lon()), DSL.val(c.point().lat()));
                // PostGIS' KNN operator: spherical distance in metres, and index-assisted in ORDER BY.
                distance = DSL.field("{0} <-> {1}", SQLDataType.DOUBLE, LOCATION_GEOG, point);
            } else {
                point = null;
                distance = null;
            }
            if (c.hasQuery()) {
                tsQuery = DSL.field("websearch_to_tsquery('english', {0})", Object.class, DSL.val(c.query()));
                rank = DSL.field("ts_rank_cd({0}, {1})", SQLDataType.REAL, T.SEARCH_VECTOR, tsQuery);
            } else {
                tsQuery = null;
                rank = null;
            }
        }

        SortKey<?> sortKey() {
            boolean desc = criteria.sort().descending();
            return switch (criteria.sort()) {
                case RELEVANCE -> new SortKey<>(rank, desc, Float::valueOf, String::valueOf, SQLDataType.REAL);
                case DISTANCE -> new SortKey<>(distance, desc, Double::valueOf, String::valueOf, SQLDataType.DOUBLE);
                case PRICE_ASC, PRICE_DESC -> new SortKey<>(T.PRICE, desc, BigDecimal::new,
                        BigDecimal::toPlainString, T.PRICE.getDataType());
                case RATING -> new SortKey<>(T.AVG_RATING, desc, BigDecimal::new, BigDecimal::toPlainString,
                        T.AVG_RATING.getDataType());
                case NEWEST -> new SortKey<>(T.CREATED_AT, desc, OffsetDateTime::parse, OffsetDateTime::toString,
                        T.CREATED_AT.getDataType());
            };
        }
    }

    /**
     * A sort key plus the tour id as tie-breaker, both in the same direction. That makes the order
     * total and lets the keyset condition be one row-value comparison, which PostgreSQL can answer
     * from a composite index: {@code (key, id) > (:key, :id)} ascending, {@code <} descending.
     */
    private record SortKey<K>(Field<K> field, boolean descending, Function<String, K> parse,
                              Function<K, String> print, org.jooq.DataType<K> type) {

        List<SortField<?>> orderBy() {
            return descending ? List.of(field.desc(), T.ID.desc()) : List.of(field.asc(), T.ID.asc());
        }

        Condition after(SearchCursor cursor) {
            K value;
            try {
                value = parse.apply(cursor.key());
            } catch (RuntimeException ex) {
                throw new InvalidSearchException("cursor", "cursor is not valid");
            }
            var key = DSL.row(field, T.ID);
            var position = DSL.row(DSL.val(value, type), DSL.val(cursor.id()));
            return descending ? key.lt(position) : key.gt(position);
        }

        String format(Object value) {
            return print.apply(type.convert(value));
        }
    }
}
