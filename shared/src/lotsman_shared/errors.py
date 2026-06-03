# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Base domain error and FastAPI exception handler factory for Лоцман.

DomainError is the base for all typed domain exceptions across services.
Subclasses declare their HTTP status code mapping so the API layer can
translate errors without knowing about HTTP in the domain or application layers.

Usage — define a service-specific error::

    from lotsman_shared.errors import DomainError

    class DocumentNotFound(DomainError):
        status_code = 404
        default_message = "Document not found"

Usage — register handlers in create_app()::

    from lotsman_shared.errors import register_exception_handlers
    register_exception_handlers(app)
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Base domain error
# ---------------------------------------------------------------------------


class DomainError(Exception):
    """Base class for all typed domain errors in Лоцман services.

    Subclasses should set ``status_code``, ``code`` (a short machine-readable
    string returned in the JSON ``code`` field so the SPA can map errors to
    specific UX states — Blocker 5 / admin-channels-review), and optionally
    ``default_message``.

    The API layer translates these to HTTP responses via the registered
    exception handler — use cases and domain code never import HTTP types.
    """

    status_code: int = 400
    default_message: str = "Domain error"
    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist."""

    status_code = 404
    default_message = "Resource not found"


class ConflictError(DomainError):
    """Raised when a state mutation conflicts with existing data."""

    status_code = 409
    default_message = "Conflict"


class ValidationError(DomainError):
    """Raised when domain invariants are violated."""

    status_code = 422
    default_message = "Validation error"


class UnauthorizedError(DomainError):
    """Raised when the actor is not authenticated."""

    status_code = 401
    default_message = "Unauthorized"


class ForbiddenError(DomainError):
    """Raised when the actor lacks permission for the requested action."""

    status_code = 403
    default_message = "Forbidden"


# ---------------------------------------------------------------------------
# FastAPI exception handler factory
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Register DomainError → HTTP JSON response handlers on the FastAPI app.

    Call this in create_app() before returning the app instance.

    The response body is::

        {"detail": "<error message>", "type": "<ClassName>"}
    """

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        log.warning(
            "domain_error",
            error_type=type(exc).__name__,
            error_message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.message,
                "type": type(exc).__name__,
                "code": type(exc).code,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled_error",
            error_type=type(exc).__name__,
            error_message=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": "InternalServerError"},
        )
