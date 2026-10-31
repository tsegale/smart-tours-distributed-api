package com.smarttours.core.fx;

/**
 * A request needs a conversion that can't be made, because no usable rate exists: price bounds in a display
 * currency, which can't be turned into NAD. Showing prices never throws this; it omits the display price.
 */
public class ExchangeRatesUnavailableException extends RuntimeException {

    private final String currency;

    public ExchangeRatesUnavailableException(String currency, String reason) {
        super(reason);
        this.currency = currency;
    }

    public String getCurrency() {
        return currency;
    }
}
