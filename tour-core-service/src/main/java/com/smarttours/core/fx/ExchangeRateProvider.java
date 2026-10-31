package com.smarttours.core.fx;

import java.util.Set;

/**
 * Port: where daily reference rates come from. Called only by the scheduled refresh, never on the
 * request path. Implementations apply their own resilience policy and throw when no usable answer came.
 */
public interface ExchangeRateProvider {

    /** The latest rate of each quote currency against {@code base}: 1 base = rate quote. */
    FetchedRates latest(String base, Set<String> quotes);
}
