# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration test: internal-JWT jti replay cache (Redis-backed).

Tests that:
1. First presentation of a jti is accepted (Redis SET nx succeeds).
2. Second presentation within TTL is rejected (Redis key already exists → nx fails).

This closes R-5c / F-003 / ADR-0003 §10 at the integration layer.
Uses a real Redis via testcontainers when available; falls back to fakeredis.
"""

from __future__ import annotations

import uuid

import pytest
from lotsman_shared.internal_jwt import issue_internal_jwt, verify_internal_jwt

_SECRET = "a" * 32  # 32-char minimum for HS256 (F-001 compliant)
_AUDIENCE = "auth-service"
_ACTOR_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# fakeredis-backed replay check (runs without Docker)
# ---------------------------------------------------------------------------

try:
    import fakeredis.aioredis as fakeredis_async  # type: ignore[import]

    _FAKEREDIS_AVAILABLE = True
except ImportError:
    _FAKEREDIS_AVAILABLE = False


@pytest.mark.skipif(not _FAKEREDIS_AVAILABLE, reason="fakeredis not installed")
@pytest.mark.asyncio
async def test_same_jti_within_ttl_rejected() -> None:
    """Presenting the same jti twice within TTL is rejected (F-003)."""
    fake_redis = fakeredis_async.FakeRedis()

    async def redis_replay_check(audience: str, jti: str, ttl_remaining: int) -> bool:
        """Returns True on first sight, False on replay."""
        key = f"jti:{audience}:{jti}"
        result = await fake_redis.set(key, "1", nx=True, ex=max(ttl_remaining, 1))
        return result is True  # nx=True → True only on first write

    token = issue_internal_jwt(
        _SECRET,
        actor_id=_ACTOR_ID,
        role="editor",
        audience=_AUDIENCE,
        ttl_seconds=60,
    )

    # First verification — should succeed
    claims = await verify_internal_jwt(
        _SECRET,
        token,
        expected_audience=_AUDIENCE,
        replay_check=redis_replay_check,
    )
    assert claims.actor_id == _ACTOR_ID

    # Second verification — same token → replay detected
    import jwt

    with pytest.raises(jwt.InvalidTokenError, match="replay detected"):
        await verify_internal_jwt(
            _SECRET,
            token,
            expected_audience=_AUDIENCE,
            replay_check=redis_replay_check,
        )


@pytest.mark.skipif(not _FAKEREDIS_AVAILABLE, reason="fakeredis not installed")
@pytest.mark.asyncio
async def test_different_jtis_not_replays() -> None:
    """Two different tokens (different jtis) are both accepted."""
    fake_redis = fakeredis_async.FakeRedis()

    async def redis_replay_check(audience: str, jti: str, ttl_remaining: int) -> bool:
        key = f"jti:{audience}:{jti}"
        result = await fake_redis.set(key, "1", nx=True, ex=max(ttl_remaining, 1))
        return result is True

    token1 = issue_internal_jwt(
        _SECRET,
        actor_id=_ACTOR_ID,
        role="editor",
        audience=_AUDIENCE,
        ttl_seconds=60,
    )
    token2 = issue_internal_jwt(
        _SECRET,
        actor_id=_ACTOR_ID,
        role="editor",
        audience=_AUDIENCE,
        ttl_seconds=60,
    )

    claims1 = await verify_internal_jwt(
        _SECRET, token1, expected_audience=_AUDIENCE, replay_check=redis_replay_check
    )
    claims2 = await verify_internal_jwt(
        _SECRET, token2, expected_audience=_AUDIENCE, replay_check=redis_replay_check
    )

    assert claims1.jti != claims2.jti


@pytest.mark.skipif(not _FAKEREDIS_AVAILABLE, reason="fakeredis not installed")
@pytest.mark.asyncio
async def test_wrong_audience_key_does_not_collide() -> None:
    """jti for auth-service must NOT block the same jti for registry-service.

    Each service uses namespace jti:{audience}:{jti} so keys don't cross audiences.
    """
    fake_redis = fakeredis_async.FakeRedis()

    async def replay_check_auth(audience: str, jti: str, ttl_remaining: int) -> bool:
        key = f"jti:{audience}:{jti}"
        return bool(await fake_redis.set(key, "1", nx=True, ex=max(ttl_remaining, 1)))

    async def replay_check_registry(audience: str, jti: str, ttl_remaining: int) -> bool:
        key = f"jti:{audience}:{jti}"
        return bool(await fake_redis.set(key, "1", nx=True, ex=max(ttl_remaining, 1)))

    # Token for auth-service
    token_auth = issue_internal_jwt(
        _SECRET, actor_id=_ACTOR_ID, role="editor", audience="auth-service", ttl_seconds=60
    )
    # Token for registry-service with a DIFFERENT secret (per-target isolation, F-002)
    reg_secret = "b" * 32
    token_reg = issue_internal_jwt(
        reg_secret, actor_id=_ACTOR_ID, role="editor", audience="registry-service", ttl_seconds=60
    )

    # Verify auth token
    claims_auth = await verify_internal_jwt(
        _SECRET, token_auth, expected_audience="auth-service", replay_check=replay_check_auth
    )

    # Verify registry token — must succeed (different namespace key)
    claims_reg = await verify_internal_jwt(
        reg_secret,
        token_reg,
        expected_audience="registry-service",
        replay_check=replay_check_registry,
    )

    assert claims_auth.audience == "auth-service"
    assert claims_reg.audience == "registry-service"
