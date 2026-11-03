"""Errors as RFC 9457 Problem Details (application/problem+json), like every other service."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"
PROBLEM_BASE = "https://smarttours.dev/problems/"
SLUGS = {403: "forbidden", 404: "not-found", 409: "conflict"}


def problem(status: int, slug: str, title: str, detail: str, request: Request, **extra) -> JSONResponse:
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
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return problem(422, "validation", "Request validation failed", "One or more fields are invalid", request,
                       errors=errors)  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug = SLUGS.get(exc.status_code, "http-error")
        return problem(exc.status_code, slug, str(exc.detail), str(exc.detail), request)
