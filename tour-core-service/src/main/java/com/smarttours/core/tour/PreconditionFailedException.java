package com.smarttours.core.tour;

/** If-Match named a version that is no longer current (RFC 9110: 412 Precondition Failed). */
public class PreconditionFailedException extends RuntimeException {

    private final long tourId;
    private final String currentEtag;

    public PreconditionFailedException(long tourId, String currentEtag) {
        super("Tour " + tourId + " has changed since it was read; GET it again and reapply the change");
        this.tourId = tourId;
        this.currentEtag = currentEtag;
    }

    public long getTourId() {
        return tourId;
    }

    /** Null when the conflict was detected at flush time and the new version is not known here. */
    public String getCurrentEtag() {
        return currentEtag;
    }
}
