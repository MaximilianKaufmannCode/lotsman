# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: notification-service app factory."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notification_service.main import create_app

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
    "INTERNAL_JWT_KEY_NOTIFICATION": "0" * 32,
    "REDIS_URL": "redis://localhost:6379/0",
}


@pytest.fixture(autouse=True)
def _required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    import notification_service.config as cfg
    monkeypatch.setattr(cfg, "_settings", None)


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
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
