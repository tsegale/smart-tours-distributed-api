package com.smarttours.core.common;

/** Entity-tag comparison for conditional writes (RFC 9110, section 13.1.1). */
public final class ETags {

    private ETags() {
    }

    /**
     * True when an If-Match header value matches {@code currentEtag}. If-Match uses the strong
     * comparison: a weak tag (W/"...") never matches, and "*" matches any existing resource.
     */
    public static boolean ifMatch(String headerValue, String currentEtag) {
        for (String candidate : headerValue.split(",")) {
            String tag = candidate.trim();
            if (tag.equals("*") || tag.equals(currentEtag)) {
                return true;
            }
        }
        return false;
    }
}
