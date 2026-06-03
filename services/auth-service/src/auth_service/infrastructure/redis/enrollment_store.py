# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed TOTP enrollment pending store (ADR-0003 §3).

Key format: ``totp:pending:{user_id}``
TTL: 5 minutes (300 seconds).
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

_ENROLLMENT_TTL_SECONDS = 300  # 5 minutes


class RedisTotpEnrollmentStore:
    """Implements auth_service.application.ports.RedisTotpEnrollmentStore."""

    _PREFIX = "totp:pending"

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, user_id: uuid.UUID) -> str:
        return f"{self._PREFIX}:{user_id}"

    async def set_pending(self, user_id: uuid.UUID, secret_b32: str) -> None:
        """Overwrite any prior pending secret (re-enroll scenario)."""
        await self._redis.set(self._key(user_id), secret_b32, ex=_ENROLLMENT_TTL_SECONDS)

    async def get_pending(self, user_id: uuid.UUID) -> str | None:
        value = await self._redis.get(self._key(user_id))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def delete_pending(self, user_id: uuid.UUID) -> None:
        await self._redis.delete(self._key(user_id))
