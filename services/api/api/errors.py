"""Consistent error envelope: {"error": {"code", "message", "detail"}}.

Every message says what went wrong AND what to do about it. An error a
frontend developer cannot act on at 3 AM is a bug in the API, not in them.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import ProgrammingError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import get_logger

log = get_logger("api.errors")


def error_body(code: str, message: str, detail: object = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail}}


class ApiError(Exception):
    """Raised deliberately by handlers to produce a specific client error."""

    def __init__(self, code: str, message: str, status_code: int = 400, detail: object = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "invalid_request",
                "The request body or query parameters did not validate. "
                "Check the field list in `detail` against /docs.",
                exc.errors(),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                "not_found" if exc.status_code == 404 else "http_error",
                str(exc.detail),
                None,
            ),
        )

    @app.exception_handler(ProgrammingError)
    async def _schema_missing(request: Request, exc: ProgrammingError) -> JSONResponse:
        """A database that was never migrated is a deployment step, not a crash.

        Returning an opaque 500 for this sends whoever deployed it hunting
        through logs for what is really a one-line answer: the schema is not
        there yet. Every database-backed endpoint fails this way at once on a
        fresh deployment, so it is worth naming precisely.
        """
        message = str(exc.orig) if exc.orig else str(exc)
        if "does not exist" in message and "relation" in message:
            log.error("schema_missing", path=str(request.url.path))
            return JSONResponse(
                status_code=503,
                content=error_body(
                    "schema_missing",
                    "The database has no schema yet, so there is nothing to serve. "
                    "Apply it: create the PostGIS extension, then restore the seed "
                    "(see DEPLOY_WALKTHROUGH.md steps 2 and 3). Endpoints that do "
                    "not touch the database, such as /v1/geocode, keep working.",
                    {"detail": message[:200]},
                ),
            )
        log.error("database_error", path=str(request.url.path), error=message[:300])
        return JSONResponse(
            status_code=500,
            content=error_body(
                "database_error",
                "A database query failed. Check the API logs for the statement.",
                {"path": str(request.url.path)},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", path=str(request.url.path), error=str(exc))
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "Something failed inside the API. The failure has been logged; "
                "retry once, then report the path and time if it persists.",
                {"path": str(request.url.path)},
            ),
        )
