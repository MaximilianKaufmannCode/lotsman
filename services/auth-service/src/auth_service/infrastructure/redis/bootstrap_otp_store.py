# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed bootstrap OTP store (ADR-0004 §3).

Key format: ``bootstrap:otp:<email>``
TTL: 24 hours (86400 seconds).

This is intentionally separate from RedisAdminOtpStore (which uses
``admin:otp:<user_id>`` with a 10-minute TTL) so that bootstrap keys
are easy to grep/distinguish and have the correct longer TTL.
"""

from __future__ import annotations

import redis.asyncio as aioredis

_BOOTSTRAP_OTP_TTL_SECONDS = 86400  # 24 hours


class RedisBootstrapOtpStore:
    """Implements auth_service.application.ports.RedisBootstrapOtpStore."""

    _PREFIX = "bootstrap:otp"

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def _key(self, email: str) -> str:
        return f"{self._PREFIX}:{email.strip().lower()}"

    async def set_otp(self, email: str, otp_hash: str) -> None:
        """Store argon2id hash of the OTP with 24-hour TTL, overwriting any prior value."""
        await self._redis.set(self._key(email), otp_hash, ex=_BOOTSTRAP_OTP_TTL_SECONDS)

    async def get_otp_hash(self, email: str) -> str | None:
        value = await self._redis.get(self._key(email))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def delete_otp(self, email: str) -> None:
        await self._redis.delete(self._key(email))
