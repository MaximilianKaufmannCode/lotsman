# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ReactivateUser use case — US-104 (admin-user-management-v2).

Restores a soft-deleted user: clears deleted_at, sets is_active=True, removes
Redis lockout flag, emits UserActivated. Mirror of DeactivateUser.

Guards:
- Cannot reactivate self (defensive — admin cannot deactivate self either, so
  this branch is unreachable in practice, but kept for symmetry).
- Cannot reactivate a user whose current `email` collides with another *active*
  user (the unique-active-email index `users_email_active_uidx` would reject
  the UPDATE). Surfaced as EmailConflictError → 409.

Idempotent: already-active users emit no duplicate event.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ReactivateUserCommand
from auth_service.application.ports import (
    EventOutbox,
    RedisLockoutStore,
    UserRepository,
)
from auth_service.domain.errors import (
    SelfActionForbiddenError,
    SystemAccountProtectedError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from auth_service.domain.events import UserActivated

_SYSTEM_EMAIL_SUFFIX = "@system.lotsman"


@dataclass(slots=True)
class ReactivateUser:
    user_repo: UserRepository
    lockout_store: RedisLockoutStore
    outbox: EventOutbox

    async def execute(self, *, cmd: ReactivateUserCommand) -> None:
        if cmd.actor_id == cmd.target_user_id:
            raise SelfActionForbiddenError("Cannot reactivate your own account")

        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        if user.email.endswith(_SYSTEM_EMAIL_SUFFIX):
            raise SystemAccountProtectedError()

        # Idempotent: already active → nothing to do, no event.
        if user.is_active and user.deleted_at is None:
            return

        # Guard email-conflict: if a different active user now owns this email,
        # we cannot restore — admin must rename / deactivate the colliding one first.
        # (`get_by_email` already filters for `deleted_at IS NULL`, so it returns
        # only active holders; if none → no conflict.)
        active_holder = await self.user_repo.get_by_email(user.email)
        if active_holder is not None and active_holder.id != user.id:
            raise UserAlreadyExistsError(
                f"Email {user.email} is held by another active user; "
                "deactivate or rename it before reactivating",
            )

        # Restore
        user.is_active = True
        user.deleted_at = None
        await self.user_repo.update(user)

        # Clear lockout flag — user becomes loginable again
        await self.lockout_store.remove_locked(cmd.target_user_id)

        # Emit event
        await self.outbox.publish(
            UserActivated(actor_id=cmd.actor_id, user_id=cmd.target_user_id).as_envelope()
        )
