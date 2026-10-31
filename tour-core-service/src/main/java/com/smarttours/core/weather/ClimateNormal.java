package com.smarttours.core.weather;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.MonthDay;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * "Typical weather" for a date beyond the forecast horizon: the same calendar window (the date ± 3 days by
 * default) in each of the last N years the archive has complete, averaged.
 *
 * <ul>
 *   <li>temperatures, rain and wind: the mean over every sampled day, to 0.1;</li>
 *   <li>precipitation probability: the share of sampled days with at least {@value #WET_DAY_MM} mm, the
 *       usual climatological "wet day" threshold (the archive has no probabilities);</li>
 *   <li>weather code: the most frequent one, the more severe (higher) code on a tie;</li>
 *   <li>sunrise and sunset: the mean time of day.</li>
 * </ul>
 */
public final class ClimateNormal {

    static final double WET_DAY_MM = 1.0;

    private ClimateNormal() {
    }

    /** One sampled window: the year and its dates. */
    public record Window(int year, LocalDate from, LocalDate to) {
    }

    /**
     * The {@code years} most recent windows that end at least {@code archiveDelayDays} before today.
     * 29 February falls back to 28 February in other years ({@link MonthDay#atYear}).
     */
    public static List<Window> windows(LocalDate date, LocalDate today, int years, int windowDays, int archiveDelayDays) {
        MonthDay monthDay = MonthDay.from(date);
        int before = (windowDays - 1) / 2;
        int after = windowDays - 1 - before;
        LocalDate latestComplete = today.minusDays(archiveDelayDays);
        List<Window> windows = new ArrayList<>(years);
        for (int year = Math.min(date.getYear(), today.getYear()); windows.size() < years; year--) {
            LocalDate centre = monthDay.atYear(year);
            LocalDate to = centre.plusDays(after);
            if (!to.isAfter(latestComplete) && centre.isBefore(date)) {
                windows.add(new Window(year, centre.minusDays(before), to));
            }
        }
        return windows;
    }

    public static WeatherReport of(GeoCell cell, LocalDate date, List<Window> sampled, List<DailyWeather> days,
                                   Instant fetchedAt) {
        if (days.isEmpty()) {
            throw new IllegalArgumentException("no days to average");
        }
        long wetDays = days.stream().map(DailyWeather::precipitationSumMm).filter(Objects::nonNull)
                .filter(mm -> mm >= WET_DAY_MM).count();
        long rainDays = days.stream().map(DailyWeather::precipitationSumMm).filter(Objects::nonNull).count();
        Integer code = mostFrequent(days.stream().map(DailyWeather::weatherCode).filter(Objects::nonNull).toList());
        int fromYear = sampled.stream().mapToInt(Window::year).min().orElseThrow();
        int toYear = sampled.stream().mapToInt(Window::year).max().orElseThrow();
        return new WeatherReport(
                WeatherReport.Type.CLIMATE_NORMAL,
                date,
                cell.latitude().doubleValue(),
                cell.longitude().doubleValue(),
                days.getFirst().timezone(),
                mean(days, DailyWeather::temperatureMaxC),
                mean(days, DailyWeather::temperatureMinC),
                rainDays == 0 ? null : (int) Math.round(100.0 * wetDays / rainDays),
                mean(days, DailyWeather::precipitationSumMm),
                mean(days, DailyWeather::windSpeedMaxKmh),
                code,
                WeatherCodes.describe(code),
                meanTime(days, DailyWeather::sunrise),
                meanTime(days, DailyWeather::sunset),
                new WeatherReport.YearRange(fromYear, toYear, sampled.size()),
                fetchedAt,
                false);
    }

    private static Double mean(List<DailyWeather> days, Function<DailyWeather, Double> value) {
        List<Double> values = days.stream().map(value).filter(Objects::nonNull).toList();
        if (values.isEmpty()) {
            return null;
        }
        double mean = values.stream().mapToDouble(Double::doubleValue).average().orElseThrow();
        return BigDecimal.valueOf(mean).setScale(1, RoundingMode.HALF_UP).doubleValue();
    }

    private static LocalTime meanTime(List<DailyWeather> days, Function<DailyWeather, LocalTime> value) {
        List<LocalTime> times = days.stream().map(value).filter(Objects::nonNull).toList();
        if (times.isEmpty()) {
            return null;
        }
        long meanSecond = Math.round(times.stream().mapToInt(LocalTime::toSecondOfDay).average().orElseThrow());
        return LocalTime.ofSecondOfDay(meanSecond).withSecond(0);
    }

    private static Integer mostFrequent(List<Integer> codes) {
        Map<Integer, Long> counts = codes.stream().collect(Collectors.groupingBy(Function.identity(), Collectors.counting()));
        return counts.entrySet().stream()
                .max(Comparator.<Map.Entry<Integer, Long>>comparingLong(Map.Entry::getValue)
                        .thenComparing(Map.Entry::getKey))
                .map(Map.Entry::getKey)
                .orElse(null);
    }
}
