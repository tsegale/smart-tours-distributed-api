package com.smarttours.core.tour;

import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.tour.dto.CreateTourRequest;
import com.smarttours.core.tour.dto.PageResponse;
import com.smarttours.core.tour.dto.TourResponse;
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
    public TourResponse create(CreateTourRequest request) {
        // JTS coordinates are (x, y) = (longitude, latitude). Swapping them is the classic PostGIS bug.
        Point location = GEOMETRY.createPoint(new Coordinate(request.longitude(), request.latitude()));
        Tour saved = tours.save(new Tour(request.name(), request.description(), request.price(), location));

        // Published to RabbitMQ only after this transaction commits (see TourEventPublisher).
        events.publishEvent(TourCreatedEvent.of(saved));
        return TourResponse.from(saved);
    }

    public TourResponse get(long id) {
        return tours.findById(id).map(TourResponse::from).orElseThrow(() -> new TourNotFoundException(id));
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
}
