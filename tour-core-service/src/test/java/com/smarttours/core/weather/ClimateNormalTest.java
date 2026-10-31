package com.smarttours.core.weather;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ClimateNormalTest {

    private static final LocalDate TODAY = LocalDate.parse("2026-10-31");
    private static final GeoCell SOSSUSVLEI = GeoCell.of(-24.7275, 15.3424);

    @Test
    void sameWeekOfTheLastTenCompleteYears() {
        List<ClimateNormal.Window> windows = ClimateNormal.windows(LocalDate.parse("2027-03-10"), TODAY, 10, 7, 7);

        assertThat(windows).hasSize(10);
        // March 2026 is already in the archive, so it is the newest year
        assertThat(windows.getFirst()).isEqualTo(new ClimateNormal.Window(2026,
                LocalDate.parse("2026-03-07"), LocalDate.parse("2026-03-13")));
        assertThat(windows.getLast().year()).isEqualTo(2017);
    }

    @Test
    void aWindowTheArchiveCanNotHaveYetIsSkipped() {
        // 2026-10-28 +- 3 ends on 2026-10-31, inside the 7-day archive delay: start a year earlier
        List<ClimateNormal.Window> windows = ClimateNormal.windows(LocalDate.parse("2027-10-28"), TODAY, 10, 7, 7);

        assertThat(windows.getFirst().year()).isEqualTo(2025);
        assertThat(windows).extracting(ClimateNormal.Window::year).doesNotHaveDuplicates().hasSize(10);
    }

    @Test
    void leapDayFallsBackToTheTwentyEighth() {
        List<ClimateNormal.Window> windows = ClimateNormal.windows(LocalDate.parse("2028-02-29"), TODAY, 4, 7, 7);

        assertThat(windows).extracting(ClimateNormal.Window::from).containsExactly(
                LocalDate.parse("2026-02-25"), LocalDate.parse("2025-02-25"),
                LocalDate.parse("2024-02-26"), LocalDate.parse("2023-02-25"));
    }

    @Test
    void averagesEverySampledDay() {
        List<ClimateNormal.Window> sampled = List.of(
                new ClimateNormal.Window(2024, LocalDate.parse("2024-12-01"), LocalDate.parse("2024-12-02")),
                new ClimateNormal.Window(2025, LocalDate.parse("2025-12-01"), LocalDate.parse("2025-12-02")));
        List<DailyWeather> days = List.of(
                day(30.0, 14.0, 0.0, 20.0, 0, "06:01", "19:34"),
                day(33.9, 13.7, 2.5, 30.0, 3, "06:01", "19:35"),
                day(36.3, 16.7, 0.0, 25.0, 0, "06:02", "19:36"),
                day(29.8, 14.6, 1.0, 25.1, 3, "06:00", "19:37"));

        WeatherReport normal = ClimateNormal.of(SOSSUSVLEI, LocalDate.parse("2027-12-01"), sampled, days,
                Instant.parse("2026-10-31T10:00:00Z"));

        assertThat(normal.type()).isEqualTo(WeatherReport.Type.CLIMATE_NORMAL);
        assertThat(normal.temperatureMaxC()).isEqualTo(32.5);   // 130.0 / 4
        assertThat(normal.temperatureMinC()).isEqualTo(14.8);   // 59.0 / 4 = 14.75, half up
        assertThat(normal.precipitationSumMm()).isEqualTo(0.9); // 3.5 / 4 = 0.875
        assertThat(normal.precipitationProbabilityPercent()).isEqualTo(50); // 2 of 4 days with >= 1 mm
        assertThat(normal.windSpeedMaxKmh()).isEqualTo(25.0);
        assertThat(normal.weatherCode()).isEqualTo(3);           // 0 and 3 twice each: the more severe wins
        assertThat(normal.description()).isEqualTo("Overcast");
        assertThat(normal.sunrise()).isEqualTo(LocalTime.parse("06:01"));
        assertThat(normal.sunset()).isEqualTo(LocalTime.parse("19:35"));
        assertThat(normal.years()).isEqualTo(new WeatherReport.YearRange(2024, 2025, 2));
        assertThat(normal.latitude()).isEqualTo(-24.7);
        assertThat(normal.longitude()).isEqualTo(15.3);
        assertThat(normal.stale()).isFalse();
    }

    @Test
    void missingValuesAreLeftOutOfTheAverage() {
        List<DailyWeather> days = List.of(
                day(30.0, null, null, null, null, null, null),
                day(32.0, null, null, null, null, null, null));

        WeatherReport normal = ClimateNormal.of(SOSSUSVLEI, LocalDate.parse("2027-12-01"),
                List.of(new ClimateNormal.Window(2025, LocalDate.parse("2025-12-01"), LocalDate.parse("2025-12-02"))),
                days, Instant.EPOCH);

        assertThat(normal.temperatureMaxC()).isEqualTo(31.0);
        assertThat(normal.temperatureMinC()).isNull();
        assertThat(normal.precipitationProbabilityPercent()).isNull();
        assertThat(normal.weatherCode()).isNull();
        assertThat(normal.description()).isNull();
        assertThat(normal.sunrise()).isNull();
    }

    @Test
    void cellsRoundToATenthOfADegree() {
        assertThat(GeoCell.of(-24.7275, 15.3424).key()).isEqualTo("-24.7:15.3");
        assertThat(GeoCell.of(-24.75, 15.35).key()).isEqualTo("-24.8:15.4");
        assertThat(GeoCell.of(0.04, -0.04).key()).isEqualTo("0.0:0.0");
    }

    private static DailyWeather day(Double max, Double min, Double rain, Double wind, Integer code,
                                    String sunrise, String sunset) {
        return new DailyWeather(LocalDate.parse("2025-12-01"), "Africa/Windhoek", max, min, null, rain, wind, code,
                sunrise == null ? null : LocalTime.parse(sunrise), sunset == null ? null : LocalTime.parse(sunset));
    }
}
