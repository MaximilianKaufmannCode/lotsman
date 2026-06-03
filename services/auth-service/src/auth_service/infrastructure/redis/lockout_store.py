# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed admin instant-lockout store (ADR-0003 §13).

Key format: ``locked-users:{user_id}``
No TTL — permanent until admin removes.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis


class RedisLockoutStore:
    """Implements auth_service.application.ports.RedisLockoutStore."""

    _PREFIX = "locked-users"

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, user_id: uuid.UUID) -> str:
        return f"{self._PREFIX}:{user_id}"

    async def set_locked(self, user_id: uuid.UUID) -> None:
        await self._redis.set(self._key(user_id), "1")

    async def is_locked(self, user_id: uuid.UUID) -> bool:
        return await self._redis.exists(self._key(user_id)) > 0

    async def remove_locked(self, user_id: uuid.UUID) -> None:
        await self._redis.delete(self._key(user_id))
