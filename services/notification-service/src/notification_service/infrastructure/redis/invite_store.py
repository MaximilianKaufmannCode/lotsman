# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis adapter for pending invite OTP scan.

Used by DisableChannel pre-check: if any invite:otp:* keys exist in Redis
with a TTL (i.e. non-expired), the last enabled channel cannot be disabled.
"""

from __future__ import annotations

import redis.asyncio as aioredis


class RedisInviteStore:
    """Implements RedisInviteStore protocol."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def has_pending_invites(self) -> bool:
        """Return True if any invite:otp:* key exists (non-expired) in Redis."""
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match="invite:otp:*", count=10
            )
            if keys:
                return True
            if cursor == 0:
                break
        return False
