package com.smarttours.core.common;

import com.smarttours.core.search.InvalidSearchException;
import com.smarttours.core.tour.PreconditionFailedException;
import com.smarttours.core.tour.PreconditionRequiredException;
import com.smarttours.core.tour.TourNotFoundException;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
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

    static final String PROBLEM_BASE = "https://smarttours.dev/problems/";

    @ExceptionHandler(TourNotFoundException.class)
    ProblemDetail handleTourNotFound(TourNotFoundException ex) {
        ProblemDetail problem = problem(HttpStatus.NOT_FOUND, "tour-not-found", "Tour not found", ex.getMessage());
        problem.setProperty("tourId", ex.getTourId());
        return problem;
    }

    @ExceptionHandler(PreconditionRequiredException.class)
    ProblemDetail handlePreconditionRequired(PreconditionRequiredException ex) {
        return problem(HttpStatus.PRECONDITION_REQUIRED, "precondition-required", "If-Match required",
                ex.getMessage());
    }

    /** The current ETag goes back in the header, so a client can re-read and retry knowingly. */
    @ExceptionHandler(PreconditionFailedException.class)
    ResponseEntity<ProblemDetail> handlePreconditionFailed(PreconditionFailedException ex) {
        ProblemDetail problem = problem(HttpStatus.PRECONDITION_FAILED, "precondition-failed",
                "The tour has changed", ex.getMessage());
        problem.setProperty("tourId", ex.getTourId());
        ResponseEntity.BodyBuilder response = ResponseEntity.status(HttpStatus.PRECONDITION_FAILED);
        if (ex.getCurrentEtag() != null) {
            response.header(HttpHeaders.ETAG, ex.getCurrentEtag());
        }
        return response.body(problem);
    }

    /** A concurrent write committed between our If-Match check and our flush: same outcome as a stale ETag. */
    @ExceptionHandler(OptimisticLockingFailureException.class)
    ProblemDetail handleOptimisticLock(OptimisticLockingFailureException ex) {
        return problem(HttpStatus.PRECONDITION_FAILED, "precondition-failed", "The tour has changed",
                "The tour was changed concurrently; GET it again and reapply the change");
    }

    /** Every problem with the search parameters at once, so a client can fix them in one go. */
    @ExceptionHandler(InvalidSearchException.class)
    ProblemDetail handleInvalidSearch(InvalidSearchException ex) {
        ProblemDetail problem = problem(HttpStatus.BAD_REQUEST, "invalid-search", "Invalid search parameters",
                ex.getMessage());
        problem.setProperty("errors", ex.getErrors());
        return problem;
    }

    static ProblemDetail problem(HttpStatus status, String slug, String title, String detail) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
        problem.setTitle(title);
        problem.setType(URI.create(PROBLEM_BASE + slug));
        return problem;
    }
}
