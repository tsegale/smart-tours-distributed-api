package com.smarttours.core.support;

/** Request bodies shared by the controller and integration tests. */
public final class TourFixtures {

    /** A complete, valid POST/PUT /api/tours body. */
    public static final String VALID_BODY = """
            {"name": "Sossusvlei Sunrise Dunes", "description": "Climb Dune 45 at dawn",
             "price": 1850.00, "latitude": -24.7275, "longitude": 15.3424,
             "category": "DESERT", "difficulty": "MODERATE", "durationMinutes": 300}
            """;

    private TourFixtures() {
    }
}
