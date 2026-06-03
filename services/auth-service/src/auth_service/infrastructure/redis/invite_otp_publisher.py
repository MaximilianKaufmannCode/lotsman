# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis adapter for side-channel OTP delivery (F-001 / ADR-0004 §6).

Writes the plaintext invite OTP to a one-shot Redis key
``invite:otp:{invitation_id}`` with a TTL of 600 seconds (10 minutes).

The notification-service consumer reads this key exactly once and
immediately calls DEL, so the OTP is never persisted in the outbox JSONB
or retained in Redis Streams beyond the delivery window.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis


class RedisInviteOtpPublisher:
    """Implements application.ports.InviteOtpPublisher."""

    _KEY_PREFIX = "invite:otp:"

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[misc]
        self._redis = redis

    async def publish(
        self,
        invitation_id: uuid.UUID,
        otp: str,
        ttl_seconds: int = 600,
    ) -> None:
        """Store *otp* under ``invite:otp:{invitation_id}`` with TTL.

        Uses SET NX so a duplicate publish (retry scenario) does not
        overwrite a key the consumer might be about to read.
        """
        key = f"{self._KEY_PREFIX}{invitation_id}"
        await self._redis.set(key, otp, ex=ttl_seconds, nx=True)
