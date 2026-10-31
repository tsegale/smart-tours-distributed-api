package com.smarttours.core.weather;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * A location rounded to 0.1° (about 11 km), the unit weather is fetched and cached by. Open-Meteo's models
 * are coarser than that (its best global grid is about 9 km), so tours a few kilometres apart share one
 * forecast and one cache entry instead of costing one call each.
 */
public record GeoCell(BigDecimal latitude, BigDecimal longitude) {

    public static GeoCell of(double latitude, double longitude) {
        return new GeoCell(round(latitude), round(longitude));
    }

    private static BigDecimal round(double degrees) {
        return BigDecimal.valueOf(degrees).setScale(1, RoundingMode.HALF_UP);
    }

    public String key() {
        return latitude.toPlainString() + ":" + longitude.toPlainString();
    }
}
