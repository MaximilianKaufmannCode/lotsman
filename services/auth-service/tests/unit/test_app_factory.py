# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: auth-service app factory."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_service.main import create_app

# Minimal env required by Settings (no real DB/Redis needed for factory tests)
_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
    "INTERNAL_JWT_KEY_AUTH": "a" * 32,
    "TOTP_ENC_KEY": "dGVzdC10b3RwLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcysK",
    "REDIS_URL": "redis://localhost:6379/0",
}


@pytest.fixture(autouse=True)
def _required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    # Reset singleton so it re-reads monkeypatched env
    import auth_service.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)


def test_healthz_route_registered() -> None:
    app = create_app()
    routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/healthz" in routes


def test_readyz_route_registered() -> None:
    app = create_app()
    routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/readyz" in routes


def test_metrics_route_registered() -> None:
    app = create_app()
    routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/metrics" in routes


def test_healthz_returns_200() -> None:
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
