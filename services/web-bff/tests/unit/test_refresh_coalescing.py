# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for BFF refresh-token coalescing (ADR-0003 §13 amendment 2026-05-12).

Tests that concurrent POST /api/v1/auth/refresh requests carrying the same
refresh cookie value are coalesced: upstream auth-service is called exactly ONCE
and all N callers receive identical responses.

Uses a mock AuthClient via monkeypatching — no network I/O.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import the coalescing state and helpers directly so we can manipulate them
# ---------------------------------------------------------------------------
from web_bff.api.v1.auth import (
    _COALESCE_TTL_SECONDS,
    _cookie_key,
    _refresh_cache,
    _refresh_cache_lock,
    _refresh_locks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: Any) -> Any:
    """Minimal fake settings object."""
    defaults = {
        "refresh_cookie_secure": False,
        "refresh_cookie_samesite": "lax",
        "refresh_token_ttl_seconds": 43200,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_upstream_response(access_token: str, new_refresh: str) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = {"access_token": access_token, "refresh_token": new_refresh}
    return resp


# ---------------------------------------------------------------------------
# Fixture: isolate cache state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_coalesce_state() -> Any:  # type: ignore[return]
    """Reset module-level coalescing state before/after each test."""
    _refresh_cache.clear()
    _refresh_locks.clear()
    yield
    _refresh_cache.clear()
    _refresh_locks.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_refresh_calls_upstream_exactly_once() -> None:
    """5 concurrent requests with the same cookie → 1 upstream call, 5 identical responses."""
    # Arrange
    call_count = 0
    new_refresh_token = "new-refresh-token-abc"
    old_refresh_token = "old-refresh-token-xyz"

    async def mock_refresh_tokens(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        # Simulate a small delay to expose the race window
        await asyncio.sleep(0.01)
        return _make_upstream_response("access-token-123", new_refresh_token)

    mock_auth_client = AsyncMock()
    mock_auth_client.refresh_tokens = mock_refresh_tokens

    settings = _make_settings()

    # Import the handler function
    from web_bff.api.v1.auth import refresh_tokens as refresh_handler

    # Build 5 concurrent calls using a bare Response mock
    async def call_refresh() -> dict[str, Any]:
        from starlette.responses import Response as StarletteResponse

        response = StarletteResponse()
        result = await refresh_handler(
            response=response,
            auth_client=mock_auth_client,
            refresh_token=old_refresh_token,
            settings=settings,
            request_id=None,
        )
        return result  # type: ignore[return-value]

    results = await asyncio.gather(*[call_refresh() for _ in range(5)])

    # Exactly one upstream call
    assert call_count == 1, f"Expected 1 upstream call, got {call_count}"

    # All 5 responses have the same access token
    access_tokens = {r.get("access_token") for r in results}
    assert access_tokens == {"access-token-123"}, f"Mismatched access tokens: {access_tokens}"

    # No refresh token in any response body
    for r in results:
        assert "refresh_token" not in r


@pytest.mark.asyncio
async def test_different_cookies_call_upstream_independently() -> None:
    """Two different refresh cookies each get their own upstream call."""
    call_count = 0

    async def mock_refresh_tokens(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.005)
        return _make_upstream_response(f"access-{call_count}", f"new-refresh-{call_count}")

    mock_auth_client = AsyncMock()
    mock_auth_client.refresh_tokens = mock_refresh_tokens

    settings = _make_settings()

    from starlette.responses import Response as StarletteResponse

    from web_bff.api.v1.auth import refresh_tokens as refresh_handler

    async def call_with(token: str) -> dict[str, Any]:
        result = await refresh_handler(
            response=StarletteResponse(),
            auth_client=mock_auth_client,
            refresh_token=token,
            settings=settings,
            request_id=None,
        )
        return result  # type: ignore[return-value]

    results = await asyncio.gather(call_with("cookie-A"), call_with("cookie-B"))

    # Two distinct tokens → 2 upstream calls
    assert call_count == 2


@pytest.mark.asyncio
async def test_cache_expires_after_ttl() -> None:
    """After _COALESCE_TTL_SECONDS the cache entry is evicted and a new upstream call is made."""
    call_count = 0

    async def mock_refresh_tokens(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return _make_upstream_response(f"access-{call_count}", f"new-refresh-{call_count}")

    mock_auth_client = AsyncMock()
    mock_auth_client.refresh_tokens = mock_refresh_tokens

    settings = _make_settings()

    from starlette.responses import Response as StarletteResponse

    from web_bff.api.v1.auth import refresh_tokens as refresh_handler

    old_token = "refresh-for-expiry-test"
    cache_key = _cookie_key(old_token)

    # First call populates the cache
    r1 = await refresh_handler(
        response=StarletteResponse(),
        auth_client=mock_auth_client,
        refresh_token=old_token,
        settings=settings,
        request_id=None,
    )
    assert call_count == 1

    # Manually back-date the cache entry so it appears expired
    async with _refresh_cache_lock:
        ts, body, cookies = _refresh_cache[cache_key]
        _refresh_cache[cache_key] = (ts - _COALESCE_TTL_SECONDS - 1.0, body, cookies)

    # Second call should go upstream again (cache expired)
    r2 = await refresh_handler(
        response=StarletteResponse(),
        auth_client=mock_auth_client,
        refresh_token=old_token,
        settings=settings,
        request_id=None,
    )
    assert call_count == 2


@pytest.mark.asyncio
async def test_missing_refresh_cookie_returns_401() -> None:
    """No cookie → immediate 401 without touching upstream."""
    from fastapi import HTTPException
    from starlette.responses import Response as StarletteResponse

    from web_bff.api.v1.auth import refresh_tokens as refresh_handler

    mock_auth_client = AsyncMock()
    settings = _make_settings()

    with pytest.raises(HTTPException) as exc_info:
        await refresh_handler(
            response=StarletteResponse(),
            auth_client=mock_auth_client,
            refresh_token=None,
            settings=settings,
            request_id=None,
        )

    assert exc_info.value.status_code == 401
    mock_auth_client.refresh_tokens.assert_not_called()


@pytest.mark.asyncio
async def test_upstream_error_propagates_and_no_cache() -> None:
    """Upstream 401 is propagated; failed responses are NOT cached."""
    from fastapi import HTTPException
    from starlette.responses import Response as StarletteResponse

    from web_bff.api.v1.auth import refresh_tokens as refresh_handler

    error_resp = MagicMock()
    error_resp.is_success = False
    error_resp.status_code = 401
    error_resp.json.return_value = {"detail": "Invalid credentials"}

    async def mock_refresh_tokens(**kwargs: Any) -> MagicMock:
        return error_resp

    mock_auth_client = AsyncMock()
    mock_auth_client.refresh_tokens = mock_refresh_tokens

    settings = _make_settings()
    old_token = "bad-refresh-token"

    with pytest.raises(HTTPException) as exc_info:
        await refresh_handler(
            response=StarletteResponse(),
            auth_client=mock_auth_client,
            refresh_token=old_token,
            settings=settings,
            request_id=None,
        )

    assert exc_info.value.status_code == 401

    # Nothing should be stored in the cache on error
    cache_key = _cookie_key(old_token)
    assert cache_key not in _refresh_cache


@pytest.mark.asyncio
async def test_cookie_key_never_stores_plaintext() -> None:
    """The cache key must be a SHA-256 hex digest, not the raw token."""
    import hashlib

    token = "super-secret-refresh-token"
    key = _cookie_key(token)
    expected = hashlib.sha256(token.encode()).hexdigest()
    assert key == expected
    assert token not in key  # plaintext must not appear in the key
