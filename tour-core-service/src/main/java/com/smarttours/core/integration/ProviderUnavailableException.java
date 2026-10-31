package com.smarttours.core.integration;

/**
 * A provider call failed with a checked cause (a time limit, an interrupted wait), carried unchecked so a
 * blocking caller sees one meaningful exception instead of a {@link java.util.concurrent.CompletionException}.
 */
public class ProviderUnavailableException extends RuntimeException {

    public ProviderUnavailableException(String provider, Throwable cause) {
        super(provider + " unavailable: " + cause, cause);
    }
}
