package com.smarttours.core.events;

import com.smarttours.core.config.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

import java.util.UUID;

/**
 * Bridges in-process domain events to RabbitMQ, only once the database transaction has committed.
 * That stops consumers from hearing about a tour that was rolled back.
 *
 * Known gap: if the broker is down at this moment the event is lost (the "dual write" problem).
 * booking-service already relays its events through a transactional outbox (ADR 0008); tour-core
 * gets the same in Phase 4. Until then a lost tour event is repaired by the next update of that tour.
 */
@Component
public class TourEventPublisher {

    private static final Logger log = LoggerFactory.getLogger(TourEventPublisher.class);

    private final RabbitTemplate rabbitTemplate;

    public TourEventPublisher(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onTourCreated(TourCreatedEvent event) {
        publish(TourCreatedEvent.TYPE, event, event.eventId(), event.tourId());
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onTourUpdated(TourUpdatedEvent event) {
        publish(TourUpdatedEvent.TYPE, event, event.eventId(), event.tourId());
    }

    private void publish(String routingKey, Object event, UUID eventId, long tourId) {
        try {
            rabbitTemplate.convertAndSend(RabbitMQConfig.EVENTS_EXCHANGE, routingKey, event);
            log.info("Published {} eventId={} tourId={}", routingKey, eventId, tourId);
        } catch (AmqpException ex) {
            log.error("Failed to publish {} for tourId={} (closed by the outbox in Phase 4)", routingKey, tourId, ex);
        }
    }
}
