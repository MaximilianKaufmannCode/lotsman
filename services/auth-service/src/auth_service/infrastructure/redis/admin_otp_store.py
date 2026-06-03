# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed admin OOB OTP store (ADR-0003 §5b).

OTP hash stored in Redis with 10-minute TTL, keyed by user_id.
Single-use: the use case deletes the key after successful use.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

_OTP_TTL_SECONDS = 600  # 10 minutes


class RedisAdminOtpStore:
    """Implements auth_service.application.ports.RedisAdminOtpStore."""

    _PREFIX = "admin:otp"

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, user_id: uuid.UUID) -> str:
        return f"{self._PREFIX}:{user_id}"

    async def set_otp(self, user_id: uuid.UUID, otp_hash: str) -> None:
        await self._redis.set(self._key(user_id), otp_hash, ex=_OTP_TTL_SECONDS)

    async def get_otp_hash(self, user_id: uuid.UUID) -> str | None:
        value = await self._redis.get(self._key(user_id))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def delete_otp(self, user_id: uuid.UUID) -> None:
        await self._redis.delete(self._key(user_id))
