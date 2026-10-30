package com.smarttours.core.tour;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;
import org.locationtech.jts.geom.Point;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;

/**
 * A bookable tour. Prices are stored once, in the base currency (NAD), as an exact decimal.
 * Location is a PostGIS point in WGS 84 (SRID 4326): x = longitude, y = latitude.
 *
 * <p>Ratings are owned by the database: verified reviews add to {@code rating_sum} and
 * {@code rating_count} in one SQL statement (see {@code TourRatingRepository}), and
 * {@code avg_rating} is a generated column, so they are read-only here. The generated full-text
 * {@code search_vector} column is only used by search and is not mapped at all.
 */
@Entity
@Table(name = "tours")
public class Tour {

    public static final String BASE_CURRENCY = "NAD";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 200)
    private String name;

    @Column(columnDefinition = "text")
    private String description;

    @Column(nullable = false, precision = 10, scale = 2)
    private BigDecimal price;

    @Column(nullable = false, length = 3)
    private String currency = BASE_CURRENCY;

    @Column(nullable = false, columnDefinition = "geometry(Point,4326)")
    private Point location;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Category category;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Difficulty difficulty;

    @Column(name = "duration_minutes", nullable = false)
    private int durationMinutes;

    @Column(name = "rating_count", nullable = false, insertable = false, updatable = false)
    private int ratingCount;

    @Column(name = "avg_rating", nullable = false, precision = 3, scale = 2, insertable = false, updatable = false)
    private BigDecimal avgRating;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Version
    @Column(nullable = false)
    private long version;

    protected Tour() {
        // for JPA
    }

    public Tour(String name, String description, BigDecimal price, Point location,
                Category category, Difficulty difficulty, int durationMinutes) {
        update(name, description, price, location, category, difficulty, durationMinutes);
    }

    /** Replaces every operator-editable attribute (PUT semantics). */
    public final void update(String name, String description, BigDecimal price, Point location,
                             Category category, Difficulty difficulty, int durationMinutes) {
        this.name = Objects.requireNonNull(name, "name");
        this.description = description;
        this.price = Objects.requireNonNull(price, "price");
        this.location = Objects.requireNonNull(location, "location");
        this.category = Objects.requireNonNull(category, "category");
        this.difficulty = Objects.requireNonNull(difficulty, "difficulty");
        this.durationMinutes = durationMinutes;
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public BigDecimal getPrice() { return price; }
    public String getCurrency() { return currency; }
    public Point getLocation() { return location; }
    public Category getCategory() { return category; }
    public Difficulty getDifficulty() { return difficulty; }
    public int getDurationMinutes() { return durationMinutes; }
    public int getRatingCount() { return ratingCount; }
    public BigDecimal getAvgRating() { return avgRating; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getVersion() { return version; }

    public double getLatitude() { return location.getY(); }
    public double getLongitude() { return location.getX(); }
}
