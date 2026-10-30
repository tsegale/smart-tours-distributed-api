package com.smarttours.core.tour.dto;

/**
 * A tour together with the row version it was read at. The version is the HTTP ETag, and what
 * If-Match is compared with on PUT. This is also the value cached under {@code tour:{id}}.
 */
public record VersionedTour(TourResponse tour, long version) {

    public String etag() {
        return etagOf(version);
    }

    /**
     * Strong ETag. Every change to the representation bumps the version: operator edits through
     * JPA's {@code @Version}, and new ratings in the same statement that adds them.
     */
    public static String etagOf(long version) {
        return "\"" + version + "\"";
    }
}
