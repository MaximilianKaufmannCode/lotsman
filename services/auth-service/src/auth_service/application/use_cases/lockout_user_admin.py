# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""LockoutUserAdmin use case — US-13 (admin instant kill-switch).

Sets a Redis lockout flag (no TTL) and revokes all user sessions.
Idempotent: setting an already-set flag is a no-op for the event (no duplicate event).
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import LockoutUserAdminCommand
from auth_service.application.ports import (
    EventOutbox,
    RedisLockoutStore,
    SessionRepository,
    UserRepository,
)
from auth_service.domain.errors import UserNotFoundError
from auth_service.domain.events import SessionRevokedAll, UserLocked


@dataclass(slots=True)
class LockoutUserAdmin:
    user_repo: UserRepository
    session_repo: SessionRepository
    lockout_store: RedisLockoutStore
    outbox: EventOutbox

    async def execute(self, *, cmd: LockoutUserAdminCommand) -> None:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        was_already_locked = await self.lockout_store.is_locked(cmd.target_user_id)
        await self.lockout_store.set_locked(cmd.target_user_id)

        # Revoke all sessions
        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)

        if not was_already_locked:
            await self.outbox.publish(
                UserLocked(actor_id=cmd.actor_id, target_user_id=cmd.target_user_id).as_envelope()
            )
            if revoked_count > 0:
                await self.outbox.publish(
                    SessionRevokedAll(
                        actor_id=cmd.actor_id,
                        target_user_id=cmd.target_user_id,
                        revoked_count=revoked_count,
                    ).as_envelope()
                )
