package com.smarttours.core.weather;

import java.time.Duration;

/**
 * A point in time a whole step must finish by, so each part waits only for what the parts before it left over.
 * Measured on the monotonic clock: wall-clock jumps (and the fixed clocks of tests) don't move it.
 */
public record Deadline(long nanoTime) {

    public static Deadline after(Duration budget) {
        return new Deadline(System.nanoTime() + budget.toNanos());
    }

    /** What is left, never negative. */
    public Duration remaining() {
        return Duration.ofNanos(Math.max(0, nanoTime - System.nanoTime()));
    }

    /** This deadline brought forward by {@code margin}, or now if that is already past. */
    public Deadline minus(Duration margin) {
        return new Deadline(nanoTime - margin.toNanos());
    }
}
