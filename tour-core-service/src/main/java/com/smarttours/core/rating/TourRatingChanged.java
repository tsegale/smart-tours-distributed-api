package com.smarttours.core.rating;

/** In-process notification: a tour's rating changed (the catalogue cache listens for it). */
public record TourRatingChanged(long tourId) {
}
