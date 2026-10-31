package com.smarttours.core.fx;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * {@code fx_rates}: every accepted rate, kept as history (audits, "what rate did that page show on
 * 3 May?", charts). One row per pair, publication date and source; a re-fetch of the same date overwrites
 * that day's row with the provider's latest figure for it and records when.
 */
@Repository
public class FxRateRepository {

    private static final String UPSERT = """
            INSERT INTO fx_rates (base, quote, rate, as_of, source, fetched_at)
            VALUES (:base, :quote, :rate, :asOf, :source, :fetchedAt)
            ON CONFLICT (base, quote, as_of, source) DO UPDATE
               SET rate = EXCLUDED.rate, fetched_at = EXCLUDED.fetched_at
            """;

    /** Per quote, the newest publication date, and of that the latest fetch. */
    private static final String LATEST = """
            SELECT DISTINCT ON (quote) quote, rate, as_of, source, fetched_at
              FROM fx_rates
             WHERE base = :base
             ORDER BY quote, as_of DESC, fetched_at DESC
            """;

    private record Row(String quote, BigDecimal rate, LocalDate asOf, String source, Instant fetchedAt) {
    }

    private final JdbcClient jdbc;

    public FxRateRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public void save(FetchedRates fetched, Instant fetchedAt) {
        OffsetDateTime at = fetchedAt.atOffset(ZoneOffset.UTC);
        for (FetchedRates.Rate rate : fetched.rates()) {
            jdbc.sql(UPSERT)
                    .param("base", fetched.base())
                    .param("quote", rate.quote())
                    .param("rate", rate.rate())
                    .param("asOf", rate.asOf())
                    .param("source", fetched.source())
                    .param("fetchedAt", at)
                    .update();
        }
    }

    public Optional<RateSnapshot> latest(String base) {
        List<Row> rows = jdbc.sql(LATEST).param("base", base)
                .query((rs, n) -> new Row(rs.getString("quote"), rs.getBigDecimal("rate"),
                        rs.getObject("as_of", LocalDate.class), rs.getString("source"),
                        rs.getTimestamp("fetched_at").toInstant()))
                .list();
        if (rows.isEmpty()) {
            return Optional.empty();
        }
        Map<String, RateSnapshot.Quote> quotes = rows.stream().collect(Collectors.toMap(
                Row::quote, row -> new RateSnapshot.Quote(row.rate(), row.asOf(), row.source())));
        Instant newestFetch = rows.stream().map(Row::fetchedAt).max(Comparator.naturalOrder()).orElseThrow();
        return Optional.of(new RateSnapshot(base, quotes, newestFetch));
    }

    /** When rates for {@code base} were last fetched and accepted, if ever. */
    public Optional<Instant> lastFetchedAt(String base) {
        return jdbc.sql("SELECT max(fetched_at) FROM fx_rates WHERE base = :base")
                .param("base", base)
                .query((rs, n) -> {
                    Timestamp at = rs.getTimestamp(1);
                    return at == null ? null : at.toInstant();
                })
                .optional();
    }
}
