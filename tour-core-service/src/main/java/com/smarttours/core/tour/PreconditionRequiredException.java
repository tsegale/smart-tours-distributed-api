package com.smarttours.core.tour;

/** A conditional write arrived without If-Match (RFC 6585: 428 Precondition Required). */
public class PreconditionRequiredException extends RuntimeException {

    public PreconditionRequiredException() {
        super("Send If-Match with the ETag from GET /api/tours/{id}, so a concurrent change is not overwritten");
    }
}
