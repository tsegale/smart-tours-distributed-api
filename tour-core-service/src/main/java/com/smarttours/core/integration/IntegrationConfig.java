package com.smarttours.core.integration;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;

@Configuration
public class IntegrationConfig {

    /** Injected wherever "now" or "today" decides something (rate staleness, forecast horizon), so tests can fix it. */
    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }
}
