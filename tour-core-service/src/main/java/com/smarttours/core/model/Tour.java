package main.java.com.smarttours.core.model;

import jakarta.persistence.*;
import org.locationtech.jts.geom.Point;

@Entity
@Table(name = "tours")
public class Tour {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    private String name;
    private Double price;

    // Stores the data as a PostGIS GEOMETRY type using SRID 4326 (GPS coordinates)
    @Column(columnDefinition = "geometry(Point,4326)")
    private Point location;

    public Tour() {}

    public Tour(String name, Double price, Point location) {
        this.name = name;
        this.price = price;
        this.location = location;
    }

    // standard getters and setters...
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }
    public Point getLocation() { return location; }
    public void setLocation(Point location) { this.location = location; }
}