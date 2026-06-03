# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Health and readiness router factory for Лоцман services.

Exposes:
    GET /healthz — always 200 (liveness probe). Kubernetes / Docker can restart
                   the container when this fails; it must not depend on external deps.
    GET /readyz  — 200 when all registered async checks pass, 503 otherwise.
                   Service registers checks via add_readiness_check().

Usage::

    from lotsman_shared.health import make_health_router, add_readiness_check

    # In config/startup:
    add_readiness_check("postgres", check_postgres)
    add_readiness_check("redis", check_redis)

    # In create_app():
    app.include_router(make_health_router())
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)

# Module-level registry of readiness checks. Each check is an async callable
# that returns None on success or raises an exception on failure.
_checks: dict[str, Callable[[], Awaitable[None]]] = {}


def add_readiness_check(name: str, check_fn: Callable[[], Awaitable[None]]) -> None:
    """Register an async readiness check function.

    Args:
        name: Human-readable name (used in the /readyz response body).
        check_fn: Async callable that raises on failure, returns None on success.
    """
    _checks[name] = check_fn


def clear_readiness_checks() -> None:
    """Remove all registered checks. Used in tests to reset module state."""
    _checks.clear()


def make_health_router() -> APIRouter:
    """Return a FastAPI router that exposes /healthz and /readyz.

    The router is stateless — it reads the module-level ``_checks`` dict at
    request time, so checks registered after ``include_router()`` still take effect.
    """
    router = APIRouter(tags=["health"])

    @router.get("/healthz", summary="Liveness probe")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", summary="Readiness probe")
    async def readyz() -> JSONResponse:
        results: dict[str, Any] = {}
        failed = False

        for name, check_fn in _checks.items():
            try:
                await asyncio.wait_for(check_fn(), timeout=5.0)
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"error: {exc}"
                failed = True
                log.warning("readiness_check_failed", check=name, error=str(exc))

        status_code = 503 if failed else 200
        return JSONResponse(
            content={"status": "degraded" if failed else "ok", "checks": results},
            status_code=status_code,
        )

    return router
