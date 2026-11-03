package com.smarttours.core.tour;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.ZoneId;

/**
 * @param defaultTimeZone the time zone of a tour whose request names none: where the business operates
 */
@ConfigurationProperties("smarttours.tours")
public record TourProperties(ZoneId defaultTimeZone) {
}
