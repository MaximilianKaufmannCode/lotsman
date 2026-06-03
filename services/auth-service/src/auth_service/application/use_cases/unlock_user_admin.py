# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""UnlockUserAdmin use case — US-13 (admin removes instant lockout flag).

Manual unlock: removes the Redis lockout flag.
Emits UserUnlocked event.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import UnlockUserAdminCommand
from auth_service.application.ports import EventOutbox, RedisLockoutStore, UserRepository
from auth_service.domain.errors import UserNotFoundError
from auth_service.domain.events import UserUnlocked


@dataclass(slots=True)
class UnlockUserAdmin:
    user_repo: UserRepository
    lockout_store: RedisLockoutStore
    outbox: EventOutbox

    async def execute(self, *, cmd: UnlockUserAdminCommand) -> None:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        await self.lockout_store.remove_locked(cmd.target_user_id)
        await self.outbox.publish(
            UserUnlocked(actor_id=cmd.actor_id, target_user_id=cmd.target_user_id).as_envelope()
        )
