# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Exception handler registration for auth-service.

SECURITY: All authentication-failure errors (InvalidCredentialsError and its
subclasses) return a UNIFORM HTTP 401 {"detail": "Invalid credentials"} to
prevent user enumeration. The audit log records the actual outcome.

See ADR-0003 §12 and US-2 Gherkin for the enumeration-prevention requirement.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from lotsman_shared.errors import register_exception_handlers as _register_shared

from auth_service.domain.errors import (
    AccountDeactivatedError,
    AccountLockedError,
    BackupCodeInvalidError,
    InvalidCredentialsError,
    SystemActorAccessDeniedError,
    TotpInvalidError,
)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app instance."""

    # Uniform 401 for ALL authentication failures (anti-enumeration, ADR-0003 §12)
    _UNIFORM_401_TYPES = (
        InvalidCredentialsError,
        AccountLockedError,
        AccountDeactivatedError,
        TotpInvalidError,
        BackupCodeInvalidError,
        SystemActorAccessDeniedError,
    )

    @app.exception_handler(InvalidCredentialsError)
    async def uniform_401_handler(request: Request, exc: InvalidCredentialsError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid credentials"},
        )

    # ADR-0008 D3a.4 / MF-3: strip 'input' from all 422 validation error responses.
    # FastAPI's default RequestValidationError handler echoes the submitted value
    # in the 'input' field of each error detail.  For enrollment routes this would
    # leak the live enrollment_token in the 422 body.  This handler overrides the
    # default to exclude 'input' from ALL validation error responses service-wide
    # (safe — 'input' is never needed by the SPA; the 'loc' + 'msg' are sufficient).
    _SAFE_VALIDATION_KEYS = {"type", "loc", "msg", "ctx", "url"}

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Manually strip 'input' (and any future echoed-value key) from each error.
        # Using key-filtering rather than exc.errors(include_input=False) for
        # compatibility across FastAPI/pydantic-core version ranges.
        safe_errors = [
            {k: v for k, v in error.items() if k in _SAFE_VALIDATION_KEYS}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": safe_errors},
        )

    # Re-register the shared handler for non-auth domain errors
    _register_shared(app)
