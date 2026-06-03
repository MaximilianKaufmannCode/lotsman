# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ChangeRole use case — US-19.

Changes a user's RBAC role.
Guards:
  - cannot demote the last admin.
  - cannot demote the last super_admin to any other role.
No-op if role is unchanged (no event emitted).

MIN_ADMINS / MIN_SUPER_ADMINS race condition note (F-004 / ADR-0004 §6):
    count_active_by_role() uses SELECT ... FOR UPDATE so concurrent
    deactivate/demote operations on the role rows are serialised at
    the database level, making the MIN_* invariants airtight.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ChangeRoleCommand, UserDTO
from auth_service.application.ports import EventOutbox, UserRepository
from auth_service.application.use_cases.check_min_admins import (
    CheckMinAdmins,
    CheckMinSuperAdmins,
)
from auth_service.domain.errors import UserNotFoundError
from auth_service.domain.events import UserRoleChanged


@dataclass(slots=True)
class ChangeRole:
    user_repo: UserRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: ChangeRoleCommand) -> UserDTO:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        # No-op if same role
        if user.role == cmd.new_role:
            return UserDTO.from_entity(user)

        # Guard: cannot demote last admin (emits auth.policy.violation.v1 before raising).
        # Uses CheckMinAdmins which calls count_active_by_role() with FOR UPDATE
        # to prevent the TOCTOU race (F-004).
        guard = CheckMinAdmins(user_repo=self.user_repo, outbox=self.outbox)
        await guard.guard(
            actor_id=cmd.actor_id,
            target_user_id=cmd.target_user_id,
            target_is_admin=(user.role == "admin" and cmd.new_role != "admin"),
            operation="role_change",
        )

        # Guard: cannot demote last super_admin to any non-super_admin role.
        if user.role == "super_admin" and cmd.new_role != "super_admin":
            super_guard = CheckMinSuperAdmins(user_repo=self.user_repo, outbox=self.outbox)
            await super_guard.guard(
                actor_id=cmd.actor_id,
                target_user_id=cmd.target_user_id,
                operation="role_change",
            )

        old_role = user.role
        user.role = cmd.new_role
        await self.user_repo.update(user)

        await self.outbox.publish(
            UserRoleChanged(
                actor_id=cmd.actor_id,
                user_id=cmd.target_user_id,
                before=old_role,
                after=cmd.new_role,
            ).as_envelope()
        )

        return UserDTO.from_entity(user)
