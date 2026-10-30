package com.smarttours.core.search;

import java.util.List;
import java.util.stream.Collectors;

/** Contradictory or out-of-range search parameters (400). Carries every problem, not just the first. */
public class InvalidSearchException extends RuntimeException {

    public record ParamError(String param, String message) {
    }

    private final List<ParamError> errors;

    public InvalidSearchException(List<ParamError> errors) {
        super(errors.stream().map(ParamError::message).collect(Collectors.joining("; ")));
        this.errors = List.copyOf(errors);
    }

    public InvalidSearchException(String param, String message) {
        this(List.of(new ParamError(param, message)));
    }

    public List<ParamError> getErrors() {
        return errors;
    }
}
