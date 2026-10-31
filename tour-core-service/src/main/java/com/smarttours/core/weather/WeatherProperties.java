package com.smarttours.core.weather;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;
import java.time.ZoneId;

/**
 * Departure weather (section 3 of Phase 3; ADR 0012 for the provider policy).
 *
 * @param forecastHorizonDays the last day served as a forecast is today + this (Open-Meteo: 16 forecast days)
 * @param climateYears        years averaged for a climate normal
 * @param climateWindowDays   days around the date sampled in each year (7: the same calendar week)
 * @param archiveDelayDays    the archive lags real time by a few days; years whose window ends later are skipped
 * @param forecastTtl         fresh for this long (plus or minus {@code ttlJitter})
 * @param forecastRetention   kept in Redis this long in total, to be served stale while the provider is down
 * @param climateTtl          climate normals change slowly: fresh for this long
 * @param climateRetention    and kept this long
 * @param ttlJitter           fraction of the TTL drawn at random per entry, so entries don't expire together
 * @param includeBudget       how long tour detail waits for weather with {@code include=weather}
 * @param requestBudget       how long {@code GET /weather} waits
 * @param zone                "today" for the past-date check and the horizon
 */
@ConfigurationProperties("smarttours.weather")
public record WeatherProperties(
        int forecastHorizonDays,
        int climateYears,
        int climateWindowDays,
        int archiveDelayDays,
        Duration forecastTtl,
        Duration forecastRetention,
        Duration climateTtl,
        Duration climateRetention,
        double ttlJitter,
        Duration includeBudget,
        Duration requestBudget,
        ZoneId zone) {
}
