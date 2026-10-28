"""Domain errors, rendered as RFC 9457 Problem Details (application/problem+json)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


class IdempotencyKeyReusedError(DomainError):
    status, slug, title = 422, "idempotency-key-reused", "Idempotency key reused with a different request"


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

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return _problem(
            422, "validation", "Request validation failed", "One or more fields are invalid", request, errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, "http-error", str(exc.detail), str(exc.detail), request)
