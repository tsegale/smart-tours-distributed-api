"""Domain errors, rendered as RFC 9457 Problem Details (application/problem+json)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"
PROBLEM_BASE = "https://smarttours.dev/problems/"


class DomainError(Exception):
    status: int = 400
    slug: str = "bad-request"
    title: str = "Bad request"

    def __init__(self, detail: str, **extra: object) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFoundError(DomainError):
    status, slug, title = 404, "not-found", "Resource not found"


class NotEnoughSlotsError(DomainError):
    status, slug, title = 409, "not-enough-slots", "Not enough slots available"


class ConflictError(DomainError):
    status, slug, title = 409, "conflict", "Request conflicts with current state"


class IllegalTransitionError(ConflictError):
    slug, title = "illegal-transition", "Booking cannot move to that status"


class PaymentPendingError(ConflictError):
    slug, title = "payment-in-progress", "A payment for this booking is already being completed"


class ReviewNotAllowedError(ConflictError):
    slug, title = "review-not-allowed", "This booking can't be reviewed"


class ReviewAlreadySubmittedError(ConflictError):
    slug, title = "review-already-submitted", "This booking has already been reviewed"


class TourNotFoundError(NotFoundError):
    slug, title = "tour-not-found", "Tour not found"


class UpstreamUnavailableError(DomainError):
    status, slug, title = 503, "upstream-unavailable", "A dependency is temporarily unavailable"


class PaymentProviderError(DomainError):
    status, slug, title = 502, "payment-provider-error", "The payment provider could not process the request"


class InvalidWebhookError(DomainError):
    status, slug, title = 400, "invalid-webhook", "Webhook rejected"


class IdempotencyKeyReusedError(DomainError):
    status, slug, title = 422, "idempotency-key-reused", "Idempotency key reused with a different request"


class TicketNotReadyError(NotFoundError):
    slug, title = "ticket-not-ready", "The ticket has not been issued yet"


class TicketsUnavailableError(DomainError):
    status, slug, title = 503, "tickets-unavailable", "Tickets are not available right now"


class TicketInvalidError(DomainError):
    status, slug, title = 422, "ticket-invalid", "Ticket not valid"


class TicketExpiredError(DomainError):
    status, slug, title = 410, "ticket-expired", "Ticket expired"


class WrongDayError(ConflictError):
    slug, title = "ticket-wrong-day", "Ticket is for another day"


class AlreadyCheckedInError(ConflictError):
    slug, title = "ticket-already-used", "Ticket already checked in"


class BookingNotConfirmedError(ConflictError):
    slug, title = "booking-not-confirmed", "Booking is not confirmed"


class UnauthenticatedError(DomainError):
    status, slug, title = 401, "unauthenticated", "Authentication required"


class ForbiddenError(DomainError):
    status, slug, title = 403, "forbidden", "Not allowed"


def _problem(status: int, slug: str, title: str, detail: str, request: Request, **extra) -> JSONResponse:
    body = {
        "type": PROBLEM_BASE + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        **extra,
    }
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return _problem(exc.status, exc.slug, exc.title, exc.detail, request, **exc.extra)

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # A query the server answered has a SQLSTATE (and is a bug or a conflict to surface as such); none means it was
        # never reached. That says nothing about the request, so it's a retryable 503.
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate is not None:
            raise exc
        log.error("database unavailable: %s", exc.__class__.__name__)
        response = _problem(
            503,
            "database-unavailable",
            "Temporarily unavailable",
            "Bookings can't be reached right now. Try again shortly.",
            request,
        )
        response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return _problem(
            422, "validation", "Request validation failed", "One or more fields are invalid", request, errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, "http-error", str(exc.detail), str(exc.detail), request)
