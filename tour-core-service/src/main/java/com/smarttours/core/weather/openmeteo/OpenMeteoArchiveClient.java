package com.smarttours.core.weather.openmeteo;

import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.service.annotation.GetExchange;
import org.springframework.web.service.annotation.HttpExchange;

/** Open-Meteo historical weather API ({@code archive-api.open-meteo.com}): reanalysis back to 1940. */
@HttpExchange
public interface OpenMeteoArchiveClient {

    @GetExchange("/v1/archive")
    OpenMeteoDailyResponse archive(@RequestParam("latitude") String latitude,
                                   @RequestParam("longitude") String longitude,
                                   @RequestParam("daily") String daily,
                                   @RequestParam("timezone") String timezone,
                                   @RequestParam("start_date") String startDate,
                                   @RequestParam("end_date") String endDate);
}
