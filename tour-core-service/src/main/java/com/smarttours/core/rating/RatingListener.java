package com.smarttours.core.rating;

import com.smarttours.core.messaging.ConsumerTopology;
import com.smarttours.core.messaging.EventParser;
import com.smarttours.core.messaging.RetryingHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

/** Applies verified reviews from booking-service to the tours' ratings. */
@Component
public class RatingListener {

    private static final Logger log = LoggerFactory.getLogger(RatingListener.class);

    private final EventParser parser;
    private final RetryingHandler retrying;
    private final TourRatingRepository ratings;
    private final ApplicationEventPublisher events;

    public RatingListener(EventParser parser, RetryingHandler retrying, TourRatingRepository ratings,
                          ApplicationEventPublisher events) {
        this.parser = parser;
        this.retrying = retrying;
        this.ratings = ratings;
        this.events = events;
    }

    @RabbitListener(queues = ConsumerTopology.RATING_QUEUE, containerFactory = ConsumerTopology.CONTAINER_FACTORY)
    public void onReviewSubmitted(Message message) {
        retrying.handle(ConsumerTopology.RATING_QUEUE, message, m -> {
            ReviewSubmittedEvent event = parser.parse(m, ReviewSubmittedEvent.class);
            if (ratings.addRatingOnce(event.eventId(), event.tourId(), event.rating())) {
                log.info("Rating {} added to tour {} (eventId={})", event.rating(), event.tourId(), event.eventId());
                events.publishEvent(new TourRatingChanged(event.tourId()));
            } else {
                // A duplicate delivery, or a tour this service doesn't have: nothing to apply either way.
                log.info("Rating for tour {} not applied: duplicate or unknown tour (eventId={})", event.tourId(),
                        event.eventId());
            }
        });
    }
}
