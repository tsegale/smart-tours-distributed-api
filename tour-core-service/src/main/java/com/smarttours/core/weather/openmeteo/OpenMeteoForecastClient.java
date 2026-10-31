package com.smarttours.core.weather.openmeteo;

import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.service.annotation.GetExchange;
import org.springframework.web.service.annotation.HttpExchange;

/**
 * Open-Meteo forecast API ({@code api.open-meteo.com}). Dates may run from today to today + 15
 * ({@code forecast_days} is at most 16); outside that it answers 400 with a reason. Dates are passed as
 * ISO strings: the proxy's default conversion would format a LocalDate in a locale style.
 */
@HttpExchange
public interface OpenMeteoForecastClient {

    @GetExchange("/v1/forecast")
    OpenMeteoDailyResponse forecast(@RequestParam("latitude") String latitude,
                                    @RequestParam("longitude") String longitude,
                                    @RequestParam("daily") String daily,
                                    @RequestParam("timezone") String timezone,
                                    @RequestParam("start_date") String startDate,
                                    @RequestParam("end_date") String endDate);
}
