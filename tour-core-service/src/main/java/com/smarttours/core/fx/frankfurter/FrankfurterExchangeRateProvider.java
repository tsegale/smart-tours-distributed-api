package com.smarttours.core.fx.frankfurter;

import com.smarttours.core.fx.ExchangeRateProvider;
import com.smarttours.core.fx.FetchedRates;
import com.smarttours.core.integration.Failures;
import com.smarttours.core.integration.IntegrationProperties;
import com.smarttours.core.integration.OutboundClients;
import com.smarttours.core.integration.ProviderPolicies;
import com.smarttours.core.integration.ProviderPolicy;
import com.smarttours.core.integration.ProviderUnavailableException;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CompletionException;

/**
 * Outbound adapter for Frankfurter. Every call goes through the {@value #NAME} policy (ADR 0012).
 * Runs only from the scheduled refresh, so it may block for the whole policy (attempts and backoff).
 */
@Component
public class FrankfurterExchangeRateProvider implements ExchangeRateProvider {

    public static final String NAME = "frankfurter";
    /** Rates without a providers filter are Frankfurter's blend across central banks (ADR 0011). */
    static final String SOURCE = "frankfurter:blended";

    private final FrankfurterClient client;
    private final ProviderPolicy policy;

    public FrankfurterExchangeRateProvider(OutboundClients clients, ProviderPolicies policies,
                                           IntegrationProperties properties) {
        this.client = clients.create(FrankfurterClient.class, NAME, properties.frankfurter());
        this.policy = policies.create(NAME, properties.frankfurter());
    }

    @Override
    public FetchedRates latest(String base, Set<String> quotes) {
        String quoteList = String.join(",", new TreeSet<>(quotes));
        List<FrankfurterRate> rows;
        try {
            rows = policy.call(() -> client.rates(base, quoteList)).join();
        } catch (CompletionException failed) {
            Throwable cause = Failures.unwrap(failed);
            throw cause instanceof RuntimeException unchecked ? unchecked : new ProviderUnavailableException(NAME, cause);
        }
        if (rows == null) {
            throw new IllegalStateException("Frankfurter returned an empty body");
        }
        return new FetchedRates(base, SOURCE, rows.stream()
                .filter(row -> base.equalsIgnoreCase(row.base()))
                .map(row -> new FetchedRates.Rate(row.quote() == null ? null : row.quote().toUpperCase(Locale.ROOT),
                        row.rate(), row.date()))
                .toList());
    }

    ProviderPolicy policy() {
        return policy;
    }
}
