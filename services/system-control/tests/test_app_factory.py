# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: system-control app factory."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Minimal env required by Settings (no Docker socket needed for factory tests).
_REQUIRED_ENV = {
    "INTERNAL_JWT_KEY_SYSTEM_CONTROL": "s" * 32,
}


@pytest.fixture(autouse=True)
def _required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    import system_control.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)


def test_create_app_returns_fastapi_instance() -> None:
    from system_control.main import create_app

    assert isinstance(create_app(), FastAPI)


def test_healthz_route_registered() -> None:
    from system_control.main import create_app

    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/healthz" in routes


def test_readyz_route_registered() -> None:
    from system_control.main import create_app

    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/readyz" in routes


def test_healthz_returns_200() -> None:
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_v1_ps_requires_auth() -> None:
    """Without an X-Internal-Token header, /v1/ps must return 401."""
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/v1/ps")
    assert resp.status_code == 401


def test_v1_logs_requires_auth() -> None:
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/v1/logs?service=auth-svc")
    assert resp.status_code == 401


def test_v1_restart_requires_auth() -> None:
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.post("/v1/restart-service", json={"service": "auth-svc"})
    assert resp.status_code == 401


def test_v1_backup_requires_auth() -> None:
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.post("/v1/backup-now", json={})
    assert resp.status_code == 401


def test_v1_migrate_requires_auth() -> None:
    from system_control.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.post("/v1/migrate", json={"service": "auth-svc"})
    assert resp.status_code == 401
