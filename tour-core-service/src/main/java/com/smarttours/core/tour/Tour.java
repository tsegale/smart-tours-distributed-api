package com.smarttours.core.tour;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
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

    public Tour(String name, String description, BigDecimal price, Point location) {
        this.name = Objects.requireNonNull(name, "name");
        this.description = description;
        this.price = Objects.requireNonNull(price, "price");
        this.location = Objects.requireNonNull(location, "location");
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public BigDecimal getPrice() { return price; }
    public String getCurrency() { return currency; }
    public Point getLocation() { return location; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getVersion() { return version; }

    public double getLatitude() { return location.getY(); }
    public double getLongitude() { return location.getX(); }
}
