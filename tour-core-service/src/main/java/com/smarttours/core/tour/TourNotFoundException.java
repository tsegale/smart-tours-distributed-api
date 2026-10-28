package com.smarttours.core.tour;

public class TourNotFoundException extends RuntimeException {

    private final long tourId;

    public TourNotFoundException(long tourId) {
        super("Tour " + tourId + " does not exist");
        this.tourId = tourId;
    }

    public long getTourId() {
        return tourId;
    }
}
