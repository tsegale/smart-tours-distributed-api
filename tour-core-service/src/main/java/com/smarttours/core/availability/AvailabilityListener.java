package com.smarttours.core.availability;

import com.smarttours.core.messaging.ConsumerTopology;
import com.smarttours.core.messaging.EventParser;
import com.smarttours.core.messaging.RetryingHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/**
 * Keeps {@code tour_departure_summary} in step with booking-service's departures.
 *
 * <p>Eventually consistent: search sees a seat change once the outbox relay has published it and
 * this listener has applied it, normally well under a second. booking-service stays the source of
 * truth: a booking for a departure that search still shows as available is refused there with 409.
 */
@Component
public class AvailabilityListener {

    private static final Logger log = LoggerFactory.getLogger(AvailabilityListener.class);

    private final EventParser parser;
    private final RetryingHandler retrying;
    private final DepartureSummaryRepository summaries;

    public AvailabilityListener(EventParser parser, RetryingHandler retrying, DepartureSummaryRepository summaries) {
        this.parser = parser;
        this.retrying = retrying;
        this.summaries = summaries;
    }

    @RabbitListener(queues = ConsumerTopology.AVAILABILITY_QUEUE, containerFactory = ConsumerTopology.CONTAINER_FACTORY)
    public void onAvailabilityChanged(Message message) {
        retrying.handle(ConsumerTopology.AVAILABILITY_QUEUE, message, m -> {
            AvailabilityChangedEvent event = parser.parse(m, AvailabilityChangedEvent.class);
            if (summaries.applyIfNewer(event)) {
                log.debug("Applied availability {} v{}: {} of {} left", event.availabilityId(), event.version(),
                        event.slotsLeft(), event.totalSlots());
            } else {
                log.debug("Ignored stale or duplicate availability {} v{} (eventId={})", event.availabilityId(),
                        event.version(), event.eventId());
            }
        });
    }
}
