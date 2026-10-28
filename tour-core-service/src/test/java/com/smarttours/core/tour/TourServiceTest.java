package com.smarttours.core.tour;

import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.tour.dto.CreateTourRequest;
import com.smarttours.core.tour.dto.TourResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TourServiceTest {

    @Mock
    private TourRepository tours;

    @Mock
    private ApplicationEventPublisher events;

    @InjectMocks
    private TourService service;

    @Test
    void create_persistsLocationAsLonLatPointAndPublishesEvent() {
        when(tours.save(any(Tour.class))).thenAnswer(invocation -> {
            Tour tour = invocation.getArgument(0);
            ReflectionTestUtils.setField(tour, "id", 7L);
            return tour;
        });

        TourResponse response = service.create(new CreateTourRequest(
                "Etosha Game Drive", "Big five at the waterholes", new BigDecimal("2400.00"), -18.8556, 16.3294));

        ArgumentCaptor<Tour> saved = ArgumentCaptor.forClass(Tour.class);
        verify(tours).save(saved.capture());
        // The regression this guards: location used to be set after save(), so it was never persisted.
        assertThat(saved.getValue().getLocation()).isNotNull();
        assertThat(saved.getValue().getLocation().getSRID()).isEqualTo(TourService.SRID_WGS84);
        assertThat(saved.getValue().getLocation().getX()).isEqualTo(16.3294);  // longitude
        assertThat(saved.getValue().getLocation().getY()).isEqualTo(-18.8556); // latitude

        ArgumentCaptor<TourCreatedEvent> event = ArgumentCaptor.forClass(TourCreatedEvent.class);
        verify(events).publishEvent(event.capture());
        assertThat(event.getValue().tourId()).isEqualTo(7L);
        assertThat(event.getValue().description()).isEqualTo("Big five at the waterholes");
        assertThat(event.getValue().eventId()).isNotNull();

        assertThat(response.id()).isEqualTo(7L);
        assertThat(response.latitude()).isEqualTo(-18.8556);
    }

    @Test
    void get_missingTour_throwsNotFound() {
        when(tours.findById(42L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(42L))
                .isInstanceOf(TourNotFoundException.class)
                .hasMessageContaining("42");
    }
}
