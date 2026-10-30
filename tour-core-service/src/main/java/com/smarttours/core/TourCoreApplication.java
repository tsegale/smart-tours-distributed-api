package com.smarttours.core;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class TourCoreApplication {

    public static void main(String[] args) {
        SpringApplication.run(TourCoreApplication.class, args);
    }
}
