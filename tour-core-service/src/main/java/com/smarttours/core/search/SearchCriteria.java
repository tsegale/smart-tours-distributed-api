package com.smarttours.core.search;

import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.StringJoiner;

/**
 * A validated, normalised search. Built only through {@link #from}, which collects every problem
 * with the parameters and throws them together as one 400.
 *
 * <p>Normalised means: equal searches are equal objects with equal {@link #canonical()} strings,
 * however the client spelled them (parameter order, repeated or comma-separated values, case,
 * whitespace, 1850 vs 1850.00). The canonical string is what cursors are bound to and what the
 * search cache key is hashed from.
 */
public record SearchCriteria(
        GeoPoint point,
        Double radiusMeters,
        BoundingBox bbox,
        String query,
        BigDecimal minPrice,
        BigDecimal maxPrice,
        List<Category> categories,
        List<Difficulty> difficulties,
        Integer minDuration,
        Integer maxDuration,
        BigDecimal minRating,
        DepartureWindow departures,
        SearchSort sort,
        int size) {

    public static final int DEFAULT_SIZE = 20;
    public static final int MAX_SIZE = 100;
    public static final double MAX_RADIUS_METERS = 500_000;
    public static final int MAX_QUERY_LENGTH = 200;
    public static final int MAX_SLOTS = 500;

    public record GeoPoint(double lat, double lon) {
    }

    public record BoundingBox(double minLon, double minLat, double maxLon, double maxLat) {
    }

    /** Has a departure starting in [from, to) with at least minSlots seats left. Null bounds are open. */
    public record DepartureWindow(Instant from, Instant to, int minSlots) {
    }

    public boolean hasPoint() {
        return point != null;
    }

    public boolean hasQuery() {
        return query != null;
    }

    /** Validates and normalises raw parameters. Dates are calendar days in {@code zone}. */
    public static SearchCriteria from(SearchParams p, ZoneId zone) {
        List<InvalidSearchException.ParamError> errors = new ArrayList<>();

        GeoPoint point = null;
        if (p.lat() != null || p.lon() != null) {
            if (p.lat() == null || p.lon() == null) {
                errors.add(error("lat", "lat and lon must be given together"));
            } else {
                checkRange(errors, "lat", p.lat(), -90, 90);
                checkRange(errors, "lon", p.lon(), -180, 180);
                point = new GeoPoint(p.lat(), p.lon());
            }
        }
        if (p.radius() != null) {
            if (p.lat() == null || p.lon() == null) {
                errors.add(error("radius", "radius needs lat and lon"));
            }
            if (!(p.radius() > 0 && p.radius() <= MAX_RADIUS_METERS)) {
                errors.add(error("radius", "radius must be more than 0 and at most " + (long) MAX_RADIUS_METERS));
            }
            if (p.bbox() != null) {
                errors.add(error("bbox", "radius and bbox are alternatives; send one of them"));
            }
        }
        BoundingBox bbox = p.bbox() == null ? null : bbox(errors, p.bbox());

        String query = null;
        if (p.q() != null && !p.q().isBlank()) {
            query = p.q().strip().replaceAll("\\s+", " ");
            if (query.length() > MAX_QUERY_LENGTH) {
                errors.add(error("q", "q must be at most " + MAX_QUERY_LENGTH + " characters"));
            }
        }

        BigDecimal minPrice = nonNegative(errors, "minPrice", p.minPrice());
        BigDecimal maxPrice = nonNegative(errors, "maxPrice", p.maxPrice());
        if (minPrice != null && maxPrice != null && minPrice.compareTo(maxPrice) > 0) {
            errors.add(error("minPrice", "minPrice must not be greater than maxPrice"));
        }

        if (p.minDuration() != null && p.minDuration() <= 0) {
            errors.add(error("minDuration", "minDuration must be positive"));
        }
        if (p.maxDuration() != null && p.maxDuration() <= 0) {
            errors.add(error("maxDuration", "maxDuration must be positive"));
        }
        if (p.minDuration() != null && p.maxDuration() != null && p.minDuration() > p.maxDuration()) {
            errors.add(error("minDuration", "minDuration must not be greater than maxDuration"));
        }

        BigDecimal minRating = p.minRating() == null ? null : p.minRating().stripTrailingZeros();
        if (minRating != null && (minRating.signum() < 0 || minRating.compareTo(BigDecimal.valueOf(5)) > 0)) {
            errors.add(error("minRating", "minRating must be between 0 and 5"));
        }

        DepartureWindow departures = departures(errors, p.from(), p.to(), p.minSlots(), zone);

        int size = p.size() == null ? DEFAULT_SIZE : p.size();
        if (size < 1 || size > MAX_SIZE) {
            errors.add(error("size", "size must be between 1 and " + MAX_SIZE));
        }

        SearchSort sort = p.sort() != null ? p.sort()
                : query != null ? SearchSort.RELEVANCE
                : point != null ? SearchSort.DISTANCE
                : SearchSort.NEWEST;
        if (sort == SearchSort.RELEVANCE && query == null) {
            errors.add(error("sort", "sort=relevance needs q"));
        }
        if (sort == SearchSort.DISTANCE && (p.lat() == null || p.lon() == null)) {
            errors.add(error("sort", "sort=distance needs lat and lon"));
        }

        if (!errors.isEmpty()) {
            throw new InvalidSearchException(errors);
        }
        return new SearchCriteria(point, p.radius(), bbox, query, minPrice, maxPrice,
                sortedDistinct(p.category()), sortedDistinct(p.difficulty()), p.minDuration(), p.maxDuration(),
                minRating, departures, sort, size);
    }

    /**
     * Every filter and the sort, in a fixed order with normalised values. Page size is left out:
     * a client may change it between pages without invalidating its cursor.
     */
    public String canonicalFilters() {
        StringJoiner out = new StringJoiner(";");
        if (point != null) {
            out.add("point=" + point.lat() + "," + point.lon());
        }
        if (radiusMeters != null) {
            out.add("radius=" + radiusMeters);
        }
        if (bbox != null) {
            out.add("bbox=" + bbox.minLon() + "," + bbox.minLat() + "," + bbox.maxLon() + "," + bbox.maxLat());
        }
        if (query != null) {
            out.add("q=" + query.toLowerCase(Locale.ROOT));
        }
        addIfPresent(out, "minPrice", minPrice == null ? null : minPrice.stripTrailingZeros().toPlainString());
        addIfPresent(out, "maxPrice", maxPrice == null ? null : maxPrice.stripTrailingZeros().toPlainString());
        if (!categories.isEmpty()) {
            out.add("category=" + String.join(",", categories.stream().map(Enum::name).toList()));
        }
        if (!difficulties.isEmpty()) {
            out.add("difficulty=" + String.join(",", difficulties.stream().map(Enum::name).toList()));
        }
        addIfPresent(out, "minDuration", minDuration);
        addIfPresent(out, "maxDuration", maxDuration);
        addIfPresent(out, "minRating", minRating == null ? null : minRating.toPlainString());
        if (departures != null) {
            out.add("departures=" + departures.from() + ".." + departures.to() + "x" + departures.minSlots());
        }
        out.add("sort=" + sort.param());
        return out.toString();
    }

    /** Canonical filters plus page size: the identity of a first page, for the search cache. */
    public String canonical() {
        return canonicalFilters() + ";size=" + size;
    }

    /** SHA-256 of the canonical filters, hex. Ties a cursor to the search that produced it. */
    public String filterFingerprint() {
        return sha256(canonicalFilters()).substring(0, 16);
    }

    public static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException("every JVM ships SHA-256", ex);
        }
    }

    private static BoundingBox bbox(List<InvalidSearchException.ParamError> errors, List<Double> values) {
        if (values.size() != 4 || values.stream().anyMatch(Objects::isNull)) {
            errors.add(error("bbox", "bbox must be four numbers: minLon,minLat,maxLon,maxLat"));
            return null;
        }
        double minLon = values.get(0), minLat = values.get(1), maxLon = values.get(2), maxLat = values.get(3);
        int before = errors.size();
        checkRange(errors, "bbox", minLon, -180, 180);
        checkRange(errors, "bbox", maxLon, -180, 180);
        checkRange(errors, "bbox", minLat, -90, 90);
        checkRange(errors, "bbox", maxLat, -90, 90);
        if (errors.size() > before) {
            return null;
        }
        if (minLat >= maxLat) {
            errors.add(error("bbox", "bbox minLat must be less than maxLat"));
        }
        if (minLon > maxLon) {
            errors.add(error("bbox", "bbox crosses the antimeridian (minLon > maxLon), which is not supported; "
                    + "split it into two searches"));
        } else if (minLon == maxLon) {
            errors.add(error("bbox", "bbox minLon must be less than maxLon"));
        }
        return new BoundingBox(minLon, minLat, maxLon, maxLat);
    }

    private static DepartureWindow departures(List<InvalidSearchException.ParamError> errors, LocalDate from,
                                              LocalDate to, Integer minSlots, ZoneId zone) {
        if (from == null && to == null && minSlots == null) {
            return null;
        }
        if (from != null && to != null && from.isAfter(to)) {
            errors.add(error("from", "from must not be after to"));
        }
        int slots = minSlots == null ? 1 : minSlots;
        if (slots < 1 || slots > MAX_SLOTS) {
            errors.add(error("minSlots", "minSlots must be between 1 and " + MAX_SLOTS));
        }
        // "to" is inclusive for the client (a calendar day), exclusive in SQL: the next midnight.
        return new DepartureWindow(
                from == null ? null : from.atStartOfDay(zone).toInstant(),
                to == null ? null : to.plusDays(1).atStartOfDay(zone).toInstant(),
                slots);
    }

    private static BigDecimal nonNegative(List<InvalidSearchException.ParamError> errors, String name,
                                          BigDecimal value) {
        if (value == null) {
            return null;
        }
        if (value.signum() < 0) {
            errors.add(error(name, name + " must not be negative"));
        }
        return value;
    }

    private static void checkRange(List<InvalidSearchException.ParamError> errors, String name, double value,
                                   double min, double max) {
        if (!(value >= min && value <= max)) {
            errors.add(error(name, name + " must be between " + (long) min + " and " + (long) max));
        }
    }

    private static <E extends Enum<E>> List<E> sortedDistinct(List<E> values) {
        return values == null ? List.of()
                : values.stream().filter(Objects::nonNull).distinct().sorted(Comparator.naturalOrder()).toList();
    }

    private static void addIfPresent(StringJoiner out, String name, Object value) {
        if (value != null) {
            out.add(name + "=" + value);
        }
    }

    private static InvalidSearchException.ParamError error(String param, String message) {
        return new InvalidSearchException.ParamError(param, message);
    }
}
