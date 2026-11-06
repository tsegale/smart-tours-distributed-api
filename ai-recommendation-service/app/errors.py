"""RFC 9457 Problem Details (application/problem+json), as in every other service."""

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

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(DomainError):
    status, slug, title = 404, "not-found", "Resource not found"


class UnauthenticatedError(DomainError):
    status, slug, title = 401, "unauthenticated", "Authentication required"


class InvalidParameterError(DomainError):
    status, slug, title = 422, "invalid-parameter", "A parameter is not valid"


def problem(status: int, slug: str, title: str, detail: str, instance: str, **extra: object) -> dict:
    return {
        "type": PROBLEM_BASE + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        **extra,
    }


def _response(status: int, slug: str, title: str, detail: str, request: Request, **extra: object) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=problem(status, slug, title, detail, request.url.path, **extra),
        media_type=PROBLEM_JSON,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return _response(exc.status, exc.slug, exc.title, exc.detail, request)

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # A query the server answered has a SQLSTATE, and is a bug to surface as one. None means it was never reached.
        if getattr(getattr(exc, "orig", None), "sqlstate", None) is not None:
            raise exc
        log.error("database unavailable: %s", exc.__class__.__name__)
        response = _response(
            503, "database-unavailable", "The database is unavailable", "Try again in a few seconds.", request
        )
        response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return _response(422, "validation-error", "The request is not valid", "See errors.", request, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(exc.status_code, "http-error", str(exc.detail), str(exc.detail), request)
