# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed email-change request store.

Stores pending email-change requests with a 15-minute TTL.
Each request carries the user_id, new_email, argon2-hashed verification
code, and a decrementing attempts counter.

Key schema: ``email_change:<request_id>`` (JSON value)
TTL: 900 seconds (15 minutes).
"""

from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis

_TTL_SECONDS = 900  # 15 minutes
_KEY_PREFIX = "email_change"


class RedisEmailChangeStore:
    """Implements auth_service.application.ports.RedisEmailChangeStore."""

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    def _key(self, request_id: str) -> str:
        return f"{_KEY_PREFIX}:{request_id}"

    async def set_request(
        self,
        request_id: str,
        *,
        user_id: uuid.UUID,
        new_email: str,
        code_hash: str,
        attempts_remaining: int,
    ) -> None:
        """Store the request with a 15-minute TTL."""
        value = json.dumps(
            {
                "user_id": str(user_id),
                "new_email": new_email,
                "code_hash": code_hash,
                "attempts_remaining": attempts_remaining,
            }
        )
        await self._redis.set(self._key(request_id), value, ex=_TTL_SECONDS)

    async def get_request(self, request_id: str) -> dict[str, object] | None:
        """Return the stored request dict or None if expired / not found."""
        raw = await self._redis.get(self._key(request_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        result: dict[str, object] = json.loads(text)
        return result

    async def delete_request(self, request_id: str) -> None:
        await self._redis.delete(self._key(request_id))

    async def decrement_attempts(self, request_id: str) -> int:
        """Decrement attempts_remaining by 1 and persist.

        Returns the new value. Does NOT delete the key even when 0 — the
        caller is responsible for deleting after the last failure.

        If the key has already expired (race), returns 0.
        """
        raw = await self._redis.get(self._key(request_id))
        if raw is None:
            return 0

        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        data = json.loads(text)
        new_attempts = max(0, int(data.get("attempts_remaining", 1)) - 1)
        data["attempts_remaining"] = new_attempts

        # Preserve remaining TTL so we don't extend the window.
        ttl: int | None = await self._redis.ttl(self._key(request_id))
        remaining_ttl = ttl if (ttl is not None and ttl > 0) else 1
        await self._redis.set(self._key(request_id), json.dumps(data), ex=remaining_ttl)
        return new_attempts
