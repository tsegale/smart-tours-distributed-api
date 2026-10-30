package com.smarttours.core.messaging;

/** The message can never be processed (malformed, wrong schema). Retrying is pointless: straight to the DLQ. */
public class PoisonMessageException extends RuntimeException {

    public PoisonMessageException(String message, Throwable cause) {
        super(message, cause);
    }

    public PoisonMessageException(String message) {
        super(message);
    }
}
