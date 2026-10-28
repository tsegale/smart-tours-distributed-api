package com.smarttours.core.common;

import com.smarttours.core.tour.TourNotFoundException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import java.net.URI;

/**
 * Every error leaves this service as RFC 9457 Problem Details (application/problem+json).
 * The base class already covers validation, malformed JSON, unsupported media types and so on.
 */
@RestControllerAdvice
public class ApiExceptionHandler extends ResponseEntityExceptionHandler {

    @ExceptionHandler(TourNotFoundException.class)
    ProblemDetail handleTourNotFound(TourNotFoundException ex) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, ex.getMessage());
        problem.setTitle("Tour not found");
        problem.setType(URI.create("https://smarttours.dev/problems/tour-not-found"));
        problem.setProperty("tourId", ex.getTourId());
        return problem;
    }
}
