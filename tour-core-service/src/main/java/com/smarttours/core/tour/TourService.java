package com.smarttours.core.tour;

import com.smarttours.core.common.ETags;
import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.events.TourUpdatedEvent;
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
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@Transactional(readOnly = true)
public class TourService {

    /** WGS 84, the coordinate system GPS uses. */
    static final int SRID_WGS84 = 4326;
    static final int MAX_NEARBY_RESULTS = 100;

    private static final GeometryFactory GEOMETRY = new GeometryFactory(new PrecisionModel(), SRID_WGS84);

    private final TourRepository tours;
    private final ApplicationEventPublisher events;

    public TourService(TourRepository tours, ApplicationEventPublisher events) {
        this.tours = tours;
        this.events = events;
    }

    @Transactional
    public VersionedTour create(TourRequest request) {
        Tour saved = tours.saveAndFlush(new Tour(request.name(), request.description(), request.price(),
                point(request), request.category(), request.difficulty(), request.durationMinutes()));

        // Published to RabbitMQ only after this transaction commits (see TourEventPublisher).
        events.publishEvent(TourCreatedEvent.of(saved));
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
                request.category(), request.difficulty(), request.durationMinutes());
        tours.flush(); // version check and bump happen now, so the response carries the new ETag

        events.publishEvent(TourUpdatedEvent.of(tour));
        return versioned(tour);
    }

    public VersionedTour get(long id) {
        return tours.findById(id).map(TourService::versioned).orElseThrow(() -> new TourNotFoundException(id));
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

    private static VersionedTour versioned(Tour tour) {
        return new VersionedTour(TourResponse.from(tour), tour.getVersion());
    }

    private static Point point(TourRequest request) {
        // JTS coordinates are (x, y) = (longitude, latitude). Swapping them is the classic PostGIS bug.
        return GEOMETRY.createPoint(new Coordinate(request.longitude(), request.latitude()));
    }
}
