# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: audit-service app factory."""

from __future__ import annotations

from fastapi import FastAPI

from audit_service.main import create_app


def test_create_app_returns_fastapi_instance() -> None:
    assert isinstance(create_app(), FastAPI)


def test_healthz_route_registered() -> None:
    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/healthz" in routes


def test_readyz_route_registered() -> None:
    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/readyz" in routes


def test_metrics_route_registered() -> None:
    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/metrics" in routes


def test_healthz_returns_200() -> None:
    # audit-service lifespan connects to Redis (starts consumer loop).
    # In unit tests we verify /healthz is registered (tested above) and
    # that the route handler itself returns 200 without the lifespan running.
    # We do this by calling the route function directly.
    import asyncio

    from lotsman_shared.health import make_health_router

    router = make_health_router()
    # Find the healthz endpoint function and call it directly.
    healthz_fn = None
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/healthz":  # type: ignore[union-attr]
            healthz_fn = route.endpoint  # type: ignore[union-attr]
            break
    assert healthz_fn is not None
    result = asyncio.run(healthz_fn())
    assert result == {"status": "ok"}
