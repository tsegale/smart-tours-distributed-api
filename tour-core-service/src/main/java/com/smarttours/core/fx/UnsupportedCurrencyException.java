package com.smarttours.core.fx;

import java.util.List;

/** {@code ?currency=} names a currency we don't display (400, with the supported ones listed). */
public class UnsupportedCurrencyException extends RuntimeException {

    private final List<String> supported;

    public UnsupportedCurrencyException(String requested, List<String> supported) {
        super("Currency '" + requested + "' is not supported; use one of " + String.join(", ", supported));
        this.supported = List.copyOf(supported);
    }

    public List<String> getSupported() {
        return supported;
    }
}
