package com.smarttours.core.common;

/** One query parameter has a value we can't serve (400 Problem Details naming it). */
public class InvalidParameterException extends RuntimeException {

    private final String param;

    public InvalidParameterException(String param, String message) {
        super(message);
        this.param = param;
    }

    public String getParam() {
        return param;
    }
}
