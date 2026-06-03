# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: web-bff app factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web_bff.main import create_app


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


def test_system_health_route_registered() -> None:
    routes = {r.path for r in create_app().routes}  # type: ignore[attr-defined]
    assert "/api/v1/system/health" in routes


def test_healthz_returns_200() -> None:
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
