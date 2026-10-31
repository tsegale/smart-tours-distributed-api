package com.smarttours.core.fx.frankfurter;

import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.service.annotation.GetExchange;
import org.springframework.web.service.annotation.HttpExchange;

import java.util.List;

/**
 * Frankfurter API v2 (https://frankfurter.dev). v1 is deprecated (its responses carry a Deprecation
 * header) and only knows the ECB's ~30 currencies, which do not include NAD.
 */
@HttpExchange
public interface FrankfurterClient {

    /**
     * {@code GET /v2/rates?base=NAD&quotes=BWP,EUR,...}: one row per quote with its own date. Without a
     * {@code providers} parameter the rate is Frankfurter's blend of every daily-publishing central bank
     * that quotes the pair.
     */
    @GetExchange("/v2/rates")
    List<FrankfurterRate> rates(@RequestParam("base") String base, @RequestParam("quotes") String quotes);
}
