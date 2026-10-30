package com.smarttours.core.tour;

import com.smarttours.core.cache.CatalogueCache;
import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.events.TourUpdatedEvent;
import com.smarttours.core.tour.dto.TourRequest;
import com.smarttours.core.tour.dto.VersionedTour;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.GeometryFactory;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TourServiceTest {

    private static final TourRequest ETOSHA = new TourRequest(
            "Etosha Game Drive", "Big five at the waterholes", new BigDecimal("2400.00"), -18.8556, 16.3294,
            Category.SAFARI, Difficulty.EASY, 480);

    @Mock
    private TourRepository tours;

    @Mock
    private ApplicationEventPublisher events;

    @Mock
    private CatalogueCache cache;

    @InjectMocks
    private TourService service;

    @Test
    void create_persistsLocationAsLonLatPointAndPublishesEvent() {
        when(tours.saveAndFlush(any(Tour.class))).thenAnswer(invocation -> {
            Tour tour = invocation.getArgument(0);
            ReflectionTestUtils.setField(tour, "id", 7L);
            return tour;
        });

        VersionedTour response = service.create(ETOSHA);

        ArgumentCaptor<Tour> saved = ArgumentCaptor.forClass(Tour.class);
        verify(tours).saveAndFlush(saved.capture());
        // The regression this guards: location used to be set after save(), so it was never persisted.
        assertThat(saved.getValue().getLocation()).isNotNull();
        assertThat(saved.getValue().getLocation().getSRID()).isEqualTo(TourService.SRID_WGS84);
        assertThat(saved.getValue().getLocation().getX()).isEqualTo(16.3294);  // longitude
        assertThat(saved.getValue().getLocation().getY()).isEqualTo(-18.8556); // latitude
        assertThat(saved.getValue().getCategory()).isEqualTo(Category.SAFARI);

        ArgumentCaptor<TourCreatedEvent> event = ArgumentCaptor.forClass(TourCreatedEvent.class);
        verify(events).publishEvent(event.capture());
        assertThat(event.getValue().tourId()).isEqualTo(7L);
        assertThat(event.getValue().description()).isEqualTo("Big five at the waterholes");
        assertThat(event.getValue().eventId()).isNotNull();

        assertThat(response.tour().id()).isEqualTo(7L);
        assertThat(response.tour().latitude()).isEqualTo(-18.8556);
        assertThat(response.tour().avgRating()).as("no reviews yet").isNull();
        assertThat(response.etag()).isEqualTo("\"0\"");
    }

    @Test
    void get_missingTour_throwsNotFound() {
        when(cache.tour(eq(42L), any())).thenAnswer(invocation -> invocation.<Supplier<?>>getArgument(1).get());
        when(tours.findById(42L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(42L))
                .isInstanceOf(TourNotFoundException.class)
                .hasMessageContaining("42");
    }

    @Test
    void update_withoutIfMatch_isRefusedBeforeLoading() {
        assertThatThrownBy(() -> service.update(7L, ETOSHA, null)).isInstanceOf(PreconditionRequiredException.class);
        assertThatThrownBy(() -> service.update(7L, ETOSHA, " ")).isInstanceOf(PreconditionRequiredException.class);
        verifyNoInteractions(tours, events, cache);
    }

    @Test
    void update_withStaleEtag_failsWithTheCurrentOne() {
        when(tours.findById(7L)).thenReturn(Optional.of(existing(3)));

        assertThatThrownBy(() -> service.update(7L, ETOSHA, "\"2\""))
                .isInstanceOfSatisfying(PreconditionFailedException.class,
                        ex -> assertThat(ex.getCurrentEtag()).isEqualTo("\"3\""));
        verify(tours, never()).flush();
        verifyNoInteractions(events);
    }

    @Test
    void update_weakEtagNeverMatches() {
        when(tours.findById(7L)).thenReturn(Optional.of(existing(3)));

        assertThatThrownBy(() -> service.update(7L, ETOSHA, "W/\"3\""))
                .isInstanceOf(PreconditionFailedException.class);
    }

    @Test
    void update_withCurrentEtag_appliesChangesAndPublishesTourUpdated() {
        Tour tour = existing(3);
        when(tours.findById(7L)).thenReturn(Optional.of(tour));

        service.update(7L, ETOSHA, "\"9\", \"3\"");

        assertThat(tour.getName()).isEqualTo("Etosha Game Drive");
        assertThat(tour.getDurationMinutes()).isEqualTo(480);
        verify(tours).flush();
        ArgumentCaptor<TourUpdatedEvent> event = ArgumentCaptor.forClass(TourUpdatedEvent.class);
        verify(events).publishEvent(event.capture());
        assertThat(event.getValue().eventType()).isEqualTo("tour.updated");
        assertThat(event.getValue().tourId()).isEqualTo(7L);
    }

    @Test
    void update_wildcardIfMatchAcceptsAnyVersion() {
        when(tours.findById(7L)).thenReturn(Optional.of(existing(5)));

        service.update(7L, ETOSHA, "*");

        verify(tours).flush();
    }

    private static Tour existing(long version) {
        Tour tour = new Tour("Old name", null, new BigDecimal("100.00"),
                new GeometryFactory().createPoint(new Coordinate(15.0, -22.0)),
                Category.CITY, Difficulty.EASY, 60);
        ReflectionTestUtils.setField(tour, "id", 7L);
        ReflectionTestUtils.setField(tour, "version", version);
        return tour;
    }
}
