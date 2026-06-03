# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ConfirmEmailChange use case — self-service email change, step 2.

Input:  ConfirmEmailChangeCommand(actor_id, request_id, verification_code)
Output: EmailChangeConfirmedDTO(email)

Business rules:
  1. Load Redis request by email_change:<request_id>.
     - Not found (expired or wrong ID) OR user_id ≠ actor_id → 404.
  2. Verify verification_code via argon2 hasher.verify.
     - Wrong code:
       * Decrement attempts_remaining.
       * If attempts_remaining == 0 → DELETE key + raise VERIFICATION_FAILED_LAST.
       * Otherwise → raise VERIFICATION_FAILED with attempts_remaining in detail.
  3. Race protection: re-query new_email NOT taken by another active user.
  4. UPDATE auth.users SET email=new_email, updated_at=now() WHERE id=actor_id.
  5. Emit auth.user.email_changed.v1 (full before/after emails — audit identifiers).
  6. DELETE Redis key.
  7. Return EmailChangeConfirmedDTO(email=new_email).

Note on JWT staleness: existing access JWTs carry the old email as a claim.
Sessions are NOT invalidated — the next token refresh will pick up the new
email. Maximum staleness = access_token_ttl_seconds (default 15 min).
This is an acceptable trade-off documented in ADR-0003 §11.2 override.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from auth_service.application.dto import (
    ConfirmEmailChangeCommand,
    EmailChangeConfirmedDTO,
)
from auth_service.application.ports import (
    EventOutbox,
    PasswordHasher,
    RedisEmailChangeStore,
    UserRepository,
)
from auth_service.domain.errors import (
    EmailAlreadyTakenError,
    EmailChangeRequestNotFoundError,
    EmailChangeVerificationFailedError,
    EmailChangeVerificationFailedLastError,
)
from auth_service.domain.events import UserEmailChanged

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class ConfirmEmailChange:
    """Step 2 of self-service email change flow."""

    user_repo: UserRepository
    email_change_store: RedisEmailChangeStore
    hasher: PasswordHasher
    outbox: EventOutbox

    async def execute(self, *, cmd: ConfirmEmailChangeCommand) -> EmailChangeConfirmedDTO:
        # Load Redis row
        row = await self.email_change_store.get_request(cmd.request_id)
        if row is None:
            raise EmailChangeRequestNotFoundError()

        # Verify ownership
        stored_user_id = uuid.UUID(str(row["user_id"]))
        if stored_user_id != cmd.actor_id:
            raise EmailChangeRequestNotFoundError()

        new_email: str = str(row["new_email"])
        code_hash: str = str(row["code_hash"])

        # Verify code
        code_ok = self.hasher.verify(code_hash, cmd.verification_code)
        if not code_ok:
            remaining = await self.email_change_store.decrement_attempts(cmd.request_id)
            if remaining == 0:
                await self.email_change_store.delete_request(cmd.request_id)
                raise EmailChangeVerificationFailedLastError()
            raise EmailChangeVerificationFailedError(attempts_remaining=remaining)

        # Race protection: re-check new_email is not taken
        existing = await self.user_repo.get_by_email(new_email)
        if existing is not None and existing.is_active and existing.id != cmd.actor_id:
            # Clean up the stale request and surface as a conflict
            await self.email_change_store.delete_request(cmd.request_id)
            raise EmailAlreadyTakenError()

        # Load actor for old email (audit) and mutation
        user = await self.user_repo.get_by_id(cmd.actor_id)
        if user is None:
            await self.email_change_store.delete_request(cmd.request_id)
            raise EmailChangeRequestNotFoundError()

        old_email = user.email

        # Mutate and persist
        user.email = new_email
        user.updated_at = datetime.now(tz=UTC)
        await self.user_repo.update(user)

        # Emit audit event — full emails are OK in audit (they are identifiers)
        await self.outbox.publish(
            UserEmailChanged(
                actor_id=cmd.actor_id,
                user_id=cmd.actor_id,
                before=old_email,
                after=new_email,
            ).as_envelope()
        )

        # Clean up Redis
        await self.email_change_store.delete_request(cmd.request_id)

        log.info(
            "email_change_confirmed",
            actor_id=str(cmd.actor_id),
            request_id=cmd.request_id,
        )

        return EmailChangeConfirmedDTO(email=new_email)
