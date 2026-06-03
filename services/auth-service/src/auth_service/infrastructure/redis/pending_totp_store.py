# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis-backed pending TOTP login ticket store.

ADR-0008 rev. 3 D1 / MF-1:

After password verification, a short-lived ticket maps:
    totp:login:pending:<ticket_id>  →  {"uid": "<uuid>", "scope": "enroll"|"login"}

The scope discriminator prevents cross-scope ticket replay:
 - Enrollment tickets (scope=enroll) are rejected by verify_totp (expects scope=login).
 - Login tickets (scope=login) are rejected by enrollment routes (expect scope=enroll).

Per-ticket confirm-attempt counter (MF-6):
    totp:login:pending:attempts:<ticket_id>  →  <integer>

TTL: 5 minutes (300 s) for both keys.
"""

from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis

from auth_service.domain.value_objects import TicketScope

_TICKET_TTL_SECONDS = 300  # 5 minutes


class RedisPendingTotpLoginStore:
    """Implements auth_service.application.ports.RedisPendingTotpLoginStore."""

    _PREFIX = "totp:login:pending"
    _ATTEMPTS_PREFIX = "totp:login:pending:attempts"

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def _key(self, ticket_id: str) -> str:
        return f"{self._PREFIX}:{ticket_id}"

    def _attempts_key(self, ticket_id: str) -> str:
        return f"{self._ATTEMPTS_PREFIX}:{ticket_id}"

    async def set_ticket(
        self,
        ticket_id: str,
        user_id: uuid.UUID,
        scope: TicketScope,
    ) -> None:
        """Store the ticket with a JSON value carrying uid + scope discriminator.

        Value shape: {"uid": "<canonical-uuid-string>", "scope": "enroll"|"login"}
        """
        value = json.dumps({"uid": str(user_id), "scope": scope.value})
        await self._redis.set(self._key(ticket_id), value, ex=_TICKET_TTL_SECONDS)

    async def get_user_id(
        self,
        ticket_id: str,
        *,
        expected_scope: TicketScope,
    ) -> uuid.UUID | None:
        """Resolve a ticket to a user UUID, only if the stored scope matches.

        Returns None for:
          - missing / expired key
          - malformed JSON (including legacy bare-string UUID values)
          - 'uid' that is not a valid UUID
          - scope mismatch (stored scope != expected_scope)

        Never raises.  Never falls back to a scope-less overload.
        """
        raw = await self._redis.get(self._key(ticket_id))
        if raw is None:
            return None
        raw_str = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            data = json.loads(raw_str)
        except (json.JSONDecodeError, ValueError):
            # Bare-string legacy values or corrupt data — treat as invalid (D1.2).
            return None
        if not isinstance(data, dict):
            return None
        stored_scope = data.get("scope")
        if stored_scope != expected_scope.value:
            # Scope mismatch — cross-scope replay rejected (D1.3 / MF-1).
            return None
        uid_str = data.get("uid")
        if not isinstance(uid_str, str):
            return None
        try:
            return uuid.UUID(uid_str)
        except ValueError:
            return None

    async def delete_ticket(self, ticket_id: str) -> None:
        """Delete the ticket key (scope-agnostic — deletion needs no discriminator)."""
        await self._redis.delete(self._key(ticket_id))

    async def increment_confirm_attempts(self, ticket_id: str) -> int:
        """INCR the per-ticket failed-confirm counter (MF-6 / D5.6.1).

        Sets TTL to _TICKET_TTL_SECONDS on first increment so the counter
        expires together with the ticket.  Returns the new counter value.
        """
        key = self._attempts_key(ticket_id)
        count: int = await self._redis.incr(key)
        if count == 1:
            # First increment — set TTL equal to the ticket TTL.
            await self._redis.expire(key, _TICKET_TTL_SECONDS)
        return count

    async def delete_confirm_attempts(self, ticket_id: str) -> None:
        """Delete the per-ticket confirm-attempt counter (called on ticket consumption)."""
        await self._redis.delete(self._attempts_key(ticket_id))
