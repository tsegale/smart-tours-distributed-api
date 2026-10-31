package com.smarttours.core.fx;

import net.javacrumbs.shedlock.core.LockProvider;
import net.javacrumbs.shedlock.provider.jdbctemplate.JdbcTemplateLockProvider;
import net.javacrumbs.shedlock.spring.annotation.EnableSchedulerLock;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.TaskScheduler;
import org.springframework.scheduling.annotation.EnableScheduling;

import javax.sql.DataSource;
import java.time.Instant;

@Configuration
@EnableScheduling
@EnableSchedulerLock(defaultLockAtMostFor = "PT10M")
public class FxSchedulingConfig {

    private final FxProperties properties;
    private final FxRefreshJob job;
    private final TaskScheduler scheduler;

    public FxSchedulingConfig(FxProperties properties, FxRefreshJob job, TaskScheduler scheduler) {
        this.properties = properties;
        this.job = job;
        this.scheduler = scheduler;
    }

    /** Lock rows in tours-db's {@code shedlock} table, timed by the database's clock, not the instances'. */
    @Bean
    LockProvider lockProvider(DataSource dataSource) {
        return new JdbcTemplateLockProvider(JdbcTemplateLockProvider.Configuration.builder()
                .withJdbcTemplate(new JdbcTemplate(dataSource))
                .usingDbTime()
                .build());
    }

    /** In the background: start-up and readiness never wait for a provider. */
    @EventListener(ApplicationReadyEvent.class)
    void refreshOnStartup() {
        if (properties.refreshOnStartup()) {
            scheduler.schedule(job::refreshIfMissing, Instant.now());
        }
    }
}
