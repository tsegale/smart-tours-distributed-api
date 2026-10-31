package com.smarttours.core.integration;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * Coalesces concurrent loads of the same key in this instance: the first caller starts the load, every
 * caller that arrives while it runs gets the same future. Once it completes, the next caller starts afresh.
 */
public final class SingleFlight<T> {

    private final ConcurrentHashMap<String, CompletableFuture<T>> inFlight = new ConcurrentHashMap<>();

    public CompletableFuture<T> run(String key, Supplier<CompletableFuture<T>> load) {
        CompletableFuture<T> mine = new CompletableFuture<>();
        CompletableFuture<T> leader = inFlight.putIfAbsent(key, mine);
        if (leader != null) {
            return leader;
        }
        try {
            load.get().whenComplete((value, failure) -> {
                inFlight.remove(key, mine);
                if (failure != null) {
                    mine.completeExceptionally(failure);
                } else {
                    mine.complete(value);
                }
            });
        } catch (RuntimeException failedToStart) {
            inFlight.remove(key, mine);
            mine.completeExceptionally(failedToStart);
        }
        return mine;
    }

    /** Loads in progress, for tests. */
    int inFlight() {
        return inFlight.size();
    }
}
