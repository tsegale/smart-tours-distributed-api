package com.smarttours.core.cache;

import com.smarttours.core.events.TourCreatedEvent;
import com.smarttours.core.events.TourUpdatedEvent;
import com.smarttours.core.rating.TourRatingChanged;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * Invalidates after the change is committed, never before: evicting first would let a concurrent
 * reader cache the old row again before the new one is visible.
 *
 * <p>Departure availability changes do NOT invalidate anything (ADR 0009). They arrive with every
 * booking, so bumping the generation on each would empty the search cache under exactly the load it
 * exists for. A cached search filtered by dates can therefore be up to the search TTL (60 s ± 10%)
 * behind the read model, which itself trails booking-service; booking-service rechecks seats on every
 * booking anyway.
 */
@Component
public class CatalogueCacheInvalidator {

    private final CatalogueCache cache;

    public CatalogueCacheInvalidator(CatalogueCache cache) {
        this.cache = cache;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onTourCreated(TourCreatedEvent event) {
        cache.catalogueChanged();
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onTourUpdated(TourUpdatedEvent event) {
        cache.tourChanged(event.tourId());
    }

    /** Published by the rating listener after its single auto-committed statement. */
    @EventListener
    public void onRatingChanged(TourRatingChanged event) {
        cache.tourChanged(event.tourId());
    }
}
