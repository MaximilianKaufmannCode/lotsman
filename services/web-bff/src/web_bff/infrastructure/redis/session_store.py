# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed short-lived session key store for web-bff.

Used to track TOTP-pending states and other transient per-request session data
that must not live in the browser (not a cookie replacement — the HttpOnly
refresh-token cookie is managed by auth-service; this store is for BFF-level
ephemeral state like "user X has passed password check, awaiting TOTP").

Keys are prefixed `bff:session:<key>` with an explicit TTL.
Implementation is a stub — real session handling is wired in the auth feature.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

_SESSION_PREFIX = "bff:session:"
_DEFAULT_TTL_SECONDS = 300  # 5 minutes for TOTP-pending state


class SessionStore:
    """Thin async wrapper around Redis for BFF session keys."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def set(
        self, key: str, value: dict[str, Any], ttl_seconds: int = _DEFAULT_TTL_SECONDS
    ) -> None:
        """Store a JSON-serialisable value under a prefixed key with TTL."""
        await self._redis.setex(
            f"{_SESSION_PREFIX}{key}",
            ttl_seconds,
            json.dumps(value),
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the stored value, or None if absent/expired."""
        raw = await self._redis.get(f"{_SESSION_PREFIX}{key}")
        if raw is None:
            return None
        decoded = raw.decode() if isinstance(raw, bytes) else raw
        result: dict[str, Any] = json.loads(decoded)
        return result

    async def delete(self, key: str) -> None:
        """Remove a session key."""
        await self._redis.delete(f"{_SESSION_PREFIX}{key}")
