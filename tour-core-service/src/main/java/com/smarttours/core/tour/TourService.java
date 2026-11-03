package com.smarttours.core.tour;

import com.smarttours.core.cache.CatalogueCache;
import com.smarttours.core.common.ETags;
import com.smarttours.core.common.InvalidParameterException;
import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.events.TourUpdatedEvent;
import com.smarttours.core.outbox.Outbox;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourRequest;
import com.smarttours.core.tour.dto.TourResponse;
import com.smarttours.core.tour.dto.VersionedTour;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;
import org.locationtech.jts.geom.PrecisionModel;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.ZoneId;
import java.util.List;

@Service
@Transactional(readOnly = true)
public class TourService {

    static final String AGGREGATE = "tour";

    /** WGS 84, the coordinate system GPS uses. */
    static final int SRID_WGS84 = 4326;
    static final int MAX_NEARBY_RESULTS = 100;

    private static final GeometryFactory GEOMETRY = new GeometryFactory(new PrecisionModel(), SRID_WGS84);

    private final TourRepository tours;
    private final ApplicationEventPublisher events;
    private final CatalogueCache cache;
    private final Outbox outbox;
    private final TourProperties properties;

    public TourService(TourRepository tours, ApplicationEventPublisher events, CatalogueCache cache, Outbox outbox,
                       TourProperties properties) {
        this.tours = tours;
        this.events = events;
        this.cache = cache;
        this.outbox = outbox;
        this.properties = properties;
    }

    @Transactional
    public VersionedTour create(TourRequest request) {
        Tour saved = tours.saveAndFlush(new Tour(request.name(), request.description(), request.price(),
                point(request), request.category(), request.difficulty(), request.durationMinutes(),
                request.meetingPoint(), timeZone(request)));

        // In this transaction: the event commits or rolls back with the tour; OutboxRelay publishes it.
        TourCreatedEvent event = TourCreatedEvent.of(saved);
        outbox.record(AGGREGATE, saved.getId(), event);
        // In-process, after commit: the catalogue cache (CatalogueCacheInvalidator).
        events.publishEvent(event);
        return versioned(saved);
    }

    /**
     * Replaces a tour, but only if the caller saw its current version (optimistic concurrency).
     * The If-Match check catches stale reads; JPA's version check at flush catches a change that
     * commits between that check and ours. Both end as 412.
     */
    @Transactional
    public VersionedTour update(long id, TourRequest request, String ifMatch) {
        if (ifMatch == null || ifMatch.isBlank()) {
            throw new PreconditionRequiredException();
        }
        Tour tour = tours.findById(id).orElseThrow(() -> new TourNotFoundException(id));
        String current = VersionedTour.etagOf(tour.getVersion());
        if (!ETags.ifMatch(ifMatch, current)) {
            throw new PreconditionFailedException(id, current);
        }
        tour.update(request.name(), request.description(), request.price(), point(request),
                request.category(), request.difficulty(), request.durationMinutes(), request.meetingPoint(),
                timeZone(request));
        tours.flush(); // version check and bump happen now, so the response carries the new ETag

        TourUpdatedEvent event = TourUpdatedEvent.of(tour);
        outbox.record(AGGREGATE, id, event);
        events.publishEvent(event);
        return versioned(tour);
    }

    /**
     * From the {@code tour} cache, else the database. No transaction around it: a cache hit must not
     * take a database connection; a miss reads in the repository's own read-only transaction.
     */
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public VersionedTour get(long id) {
        return cache.tour(id, () -> tours.findById(id).map(TourService::versioned)
                .orElseThrow(() -> new TourNotFoundException(id)));
    }

    public PageResponse<TourResponse> list(int page, int size) {
        var pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.ASC, "id"));
        return PageResponse.of(tours.findAll(pageable), TourResponse::from);
    }

    public List<TourResponse> nearby(double lat, double lon, double radiusMeters) {
        return tours.findWithinRadius(lat, lon, radiusMeters, MAX_NEARBY_RESULTS).stream()
                .map(TourResponse::from)
                .toList();
    }

    /** An IANA region id, or the default when the request names none. Fixed offsets (+02:00) are refused. */
    private ZoneId timeZone(TourRequest request) {
        String requested = request.timeZone();
        if (requested == null || requested.isBlank()) {
            return properties.defaultTimeZone();
        }
        if (!ZoneId.getAvailableZoneIds().contains(requested.strip())) {
            throw new InvalidParameterException("timeZone",
                    "timeZone must be an IANA time zone such as Africa/Windhoek, not '" + requested + "'");
        }
        return ZoneId.of(requested.strip());
    }

    private static VersionedTour versioned(Tour tour) {
        return new VersionedTour(TourResponse.from(tour), tour.getVersion());
    }

    private static Point point(TourRequest request) {
        // JTS coordinates are (x, y) = (longitude, latitude). Swapping them is the classic PostGIS bug.
        return GEOMETRY.createPoint(new Coordinate(request.longitude(), request.latitude()));
    }
}
