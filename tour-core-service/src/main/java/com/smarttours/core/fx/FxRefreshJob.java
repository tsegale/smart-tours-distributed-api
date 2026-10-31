package com.smarttours.core.fx;

import net.javacrumbs.shedlock.spring.annotation.SchedulerLock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * The daily refresh, on one instance at a time. Both entry points take the same ShedLock lock
 * ({@value #LOCK}, a row in {@code shedlock}), so N instances starting together, or a start-up that
 * coincides with the schedule, refresh once: the others find the lock held and skip.
 *
 * <ul>
 *   <li>{@code lockAtMostFor} (10 min): a refresh takes seconds even with every retry; if an instance dies
 *       holding the lock, the next run is blocked for at most this long.</li>
 *   <li>{@code lockAtLeastFor} (1 min): longer than any clock skew between instances, so an instance whose
 *       clock is a few seconds behind can't run the same daily slot again right after.</li>
 * </ul>
 */
@Component
public class FxRefreshJob {

    private static final Logger log = LoggerFactory.getLogger(FxRefreshJob.class);
    static final String LOCK = "fx-refresh";

    private final ExchangeRateRefresher refresher;

    public FxRefreshJob(ExchangeRateRefresher refresher) {
        this.refresher = refresher;
    }

    @Scheduled(cron = "${smarttours.fx.refresh-cron}", zone = "${smarttours.fx.zone}")
    @SchedulerLock(name = LOCK, lockAtMostFor = "PT10M", lockAtLeastFor = "PT1M")
    public void scheduledRefresh() {
        refresher.refresh();
    }

    /** At start-up: only if no rates were accepted today, so restarts don't hammer the provider. */
    @SchedulerLock(name = LOCK, lockAtMostFor = "PT10M", lockAtLeastFor = "PT1M")
    public void refreshIfMissing() {
        if (refresher.refreshedToday()) {
            log.info("Exchange rates already refreshed today, skipping the start-up refresh");
            return;
        }
        refresher.refresh();
    }
}
