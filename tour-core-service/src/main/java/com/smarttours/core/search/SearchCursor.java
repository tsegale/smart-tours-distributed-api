package com.smarttours.core.search;

import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

import java.util.Base64;

/**
 * Keyset position after the last row of a page: the sort key of that row and its id (the tie-breaker).
 *
 * <p>Opaque to clients: base64url of a small JSON object. It is bound to the sort order and to a
 * fingerprint of the filters, so a cursor replayed against a different search is refused (400)
 * instead of silently returning a wrong page. It is not signed: tampering can only produce a
 * different, still valid page of the same public search.
 *
 * @param key the sort key value of the last row, as text (exact for decimals, timestamps, and float
 *            and double values, which round-trip through their shortest decimal form)
 */
public record SearchCursor(int v, String s, String k, long i, String f) {

    static final int VERSION = 1;
    private static final JsonMapper JSON = JsonMapper.builder().build();

    public static SearchCursor after(SearchSort sort, String key, long id, String filterFingerprint) {
        return new SearchCursor(VERSION, sort.param(), key, id, filterFingerprint);
    }

    public String encode() {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(JSON.writeValueAsBytes(this));
    }

    /** Decodes and checks a cursor against the search it is being used with. */
    public static SearchCursor decode(String token, SearchCriteria criteria) {
        SearchCursor cursor;
        try {
            cursor = JSON.readValue(Base64.getUrlDecoder().decode(token), SearchCursor.class);
        } catch (IllegalArgumentException | JacksonException ex) {
            throw new InvalidSearchException("cursor", "cursor is not valid");
        }
        if (cursor == null || cursor.v() != VERSION || cursor.k() == null || cursor.s() == null) {
            throw new InvalidSearchException("cursor", "cursor is not valid");
        }
        if (!cursor.s().equals(criteria.sort().param()) || !criteria.filterFingerprint().equals(cursor.f())) {
            throw new InvalidSearchException("cursor",
                    "cursor belongs to a different search; send the same filters and sort as the first page");
        }
        return cursor;
    }

    public long id() {
        return i;
    }

    public String key() {
        return k;
    }
}
