package com.smarttours.core.tour.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.smarttours.core.fx.CurrencyConversion;
import com.smarttours.core.fx.DisplayPrice;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import com.smarttours.core.tour.Tour;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * A tour as clients see it. {@code avgRating} is null until the first verified review; {@code timeZone} is
 * the IANA zone its departures are in.
 *
 * <p>{@code price} is in {@code currency} (NAD), authoritative and what a booking charges. With
 * {@code ?currency=}, {@code displayPrice} adds it converted for display only, or, when no usable rate
 * exists, {@code displayPriceUnavailable} says why (ADR 0011). Both are absent otherwise, and always
 * absent from the cached value: conversion happens after the cache.
 */
public record TourResponse(
        Long id,
        String name,
        String description,
        BigDecimal price,
        String currency,
        double latitude,
        double longitude,
        Category category,
        Difficulty difficulty,
        int durationMinutes,
        String meetingPoint,
        String timeZone,
        BigDecimal avgRating,
        int ratingCount,
        Instant createdAt,
        @JsonInclude(JsonInclude.Include.NON_NULL) DisplayPrice displayPrice,
        @JsonInclude(JsonInclude.Include.NON_NULL) String displayPriceUnavailable) {

    public static TourResponse from(Tour tour) {
        return new TourResponse(
                tour.getId(),
                tour.getName(),
                tour.getDescription(),
                tour.getPrice(),
                tour.getCurrency(),
                tour.getLatitude(),
                tour.getLongitude(),
                tour.getCategory(),
                tour.getDifficulty(),
                tour.getDurationMinutes(),
                tour.getMeetingPoint(),
                tour.getTimeZone(),
                tour.getRatingCount() == 0 ? null : tour.getAvgRating(),
                tour.getRatingCount(),
                tour.getCreatedAt(),
                null,
                null);
    }

    public TourResponse priced(CurrencyConversion conversion) {
        if (!conversion.changesRepresentation()) {
            return this;
        }
        return new TourResponse(id, name, description, price, currency, latitude, longitude, category, difficulty,
                durationMinutes, meetingPoint, timeZone, avgRating, ratingCount, createdAt, conversion.display(price),
                conversion.unavailableReason());
    }
}
