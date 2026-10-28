package com.smarttours.core.events;

import com.smarttours.core.config.RabbitMQConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * Bridges in-process domain events to RabbitMQ, only once the database transaction has committed.
 * That stops consumers from hearing about a tour that was rolled back.
 *
 * Known gap: if the broker is down at this moment the event is lost (the "dual write" problem).
 * Phase 4 replaces this with a transactional outbox, which writes the event in the same
 * transaction as the tour and relays it asynchronously.
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
        try {
            rabbitTemplate.convertAndSend(RabbitMQConfig.EVENTS_EXCHANGE, TourCreatedEvent.TYPE, event);
            log.info("Published {} eventId={} tourId={}", event.eventType(), event.eventId(), event.tourId());
        } catch (AmqpException ex) {
            log.error("Failed to publish {} for tourId={} (will be fixed by the outbox in Phase 4)",
                    event.eventType(), event.tourId(), ex);
        }
    }
}
