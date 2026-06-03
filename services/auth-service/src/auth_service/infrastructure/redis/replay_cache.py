# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed JTI replay cache (ADR-0003 §10 R-5c / F-003).

Key format: ``jti:{audience}:{jti}``
TTL: ttl_remaining_seconds (time until the token expires).

The replay_check callable has signature:
    (audience: str, jti: str, ttl_remaining: int) -> bool
"""

from __future__ import annotations

import redis.asyncio as aioredis


class RedisReplayCache:
    """Implements auth_service.application.ports.RedisReplayCache."""

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, audience: str, jti: str) -> str:
        return f"jti:{audience}:{jti}"

    async def check_and_set(self, audience: str, jti: str, ttl_seconds: int) -> bool:
        """Return True if first sight (and cache it). False on replay.

        Uses SET NX (only set if key does not exist).
        """
        result = await self._redis.set(
            self._key(audience, jti),
            "1",
            nx=True,
            ex=max(1, ttl_seconds),
        )
        return result is not None  # None means key already existed (replay)

    def as_replay_check_callable(self, audience: str) -> Callable[[str, str, int], Awaitable[bool]]:  # noqa: F821
        """Return a callable compatible with verify_internal_jwt replay_check parameter."""

        async def _replay_check(aud: str, jti: str, ttl_remaining: int) -> bool:
            return await self.check_and_set(aud, jti, ttl_remaining)

        return _replay_check
