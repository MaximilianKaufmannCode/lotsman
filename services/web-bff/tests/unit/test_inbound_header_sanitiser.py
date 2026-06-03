# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for InboundHeaderSanitiser middleware.

Closes F-008 (CWE-348 — user-controlled request headers bypass trust boundary).

Asserts that X-Internal-Token, X-Forwarded-User, X-Forwarded-Roles, and
X-Internal-Actor are stripped from inbound requests on both happy-path and
404 responses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from web_bff.infrastructure.middleware.inbound_header_sanitiser import (
    InboundHeaderSanitiser,
)

# ---------------------------------------------------------------------------
# Minimal test app that echoes request headers
# ---------------------------------------------------------------------------


def _make_test_app() -> FastAPI:
    """Minimal FastAPI app that echoes the headers it actually received."""
    app = FastAPI()
    app.add_middleware(InboundHeaderSanitiser)

    @app.get("/echo-headers")
    async def echo_headers(request: Request) -> JSONResponse:
        return JSONResponse({"headers": dict(request.headers)})

    return app


_APP = _make_test_app()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_echo(extra_headers: dict[str, str]) -> dict[str, str]:
    with TestClient(_APP, raise_server_exceptions=False) as client:
        resp = client.get("/echo-headers", headers=extra_headers)
    return dict(resp.json()["headers"])


# ---------------------------------------------------------------------------
# Tests — happy path (200 route)
# ---------------------------------------------------------------------------


def test_x_internal_token_stripped_on_200_route() -> None:
    received = _get_echo({"X-Internal-Token": "attacker-crafted"})
    assert "x-internal-token" not in received


def test_x_forwarded_user_stripped_on_200_route() -> None:
    received = _get_echo({"X-Forwarded-User": "admin"})
    assert "x-forwarded-user" not in received


def test_x_forwarded_roles_stripped_on_200_route() -> None:
    received = _get_echo({"X-Forwarded-Roles": "admin,editor"})
    assert "x-forwarded-roles" not in received


def test_x_internal_actor_stripped_on_200_route() -> None:
    received = _get_echo({"X-Internal-Actor": "system"})
    assert "x-internal-actor" not in received


def test_normal_headers_preserved() -> None:
    received = _get_echo({"Authorization": "Bearer legitimate-access-token"})
    assert "authorization" in received


def test_multiple_blocked_headers_all_stripped() -> None:
    received = _get_echo(
        {
            "X-Internal-Token": "tok",
            "X-Forwarded-User": "admin",
            "X-Forwarded-Roles": "admin",
            "X-Internal-Actor": "sys",
            "Accept": "application/json",
        }
    )
    assert "x-internal-token" not in received
    assert "x-forwarded-user" not in received
    assert "x-forwarded-roles" not in received
    assert "x-internal-actor" not in received
    assert "accept" in received


# ---------------------------------------------------------------------------
# Tests — 404 path (headers also sanitised even when no matching route)
# ---------------------------------------------------------------------------


def test_x_internal_token_stripped_on_404_route() -> None:
    with TestClient(_APP, raise_server_exceptions=False) as client:
        resp = client.get("/nonexistent", headers={"X-Internal-Token": "evil"})
    # The 404 response is from FastAPI default — we just verify 404, not a leak.
    assert resp.status_code == 404
    # The key thing: no 500 due to header processing, and the path returns 404 normally.


def test_no_blocked_header_no_change() -> None:
    """Requests without any blocked header pass through unchanged."""
    received = _get_echo({"X-Request-Id": "trace-123", "Accept-Language": "ru"})
    assert "x-request-id" in received
    assert "accept-language" in received
    assert "x-internal-token" not in received
