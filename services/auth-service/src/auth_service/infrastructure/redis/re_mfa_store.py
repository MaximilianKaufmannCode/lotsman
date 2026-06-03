# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed re-MFA verification store.

Tracks recent TOTP re-MFA completions per (user_id, session_id).
TTL: 5 minutes.

Key: ``mfa-verified:{user_id}:{session_id}``
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

_RE_MFA_TTL_SECONDS = 300  # 5 minutes


class RedisReMfaStore:
    """Implements auth_service.application.ports.RedisReMfaStore."""

    _PREFIX = "mfa-verified"

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        return f"{self._PREFIX}:{user_id}:{session_id}"

    async def set_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        await self._redis.set(self._key(user_id, session_id), "1", ex=_RE_MFA_TTL_SECONDS)

    async def is_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        return await self._redis.exists(self._key(user_id, session_id)) > 0
