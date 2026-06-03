# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""DeleteUser use case — permanent (soft) removal of a user.

Soft-deletes: sets deleted_at=now() + is_active=False, revokes all sessions, sets
Redis lockout. The row is KEPT (not physically removed) so audit events and
document references (responsible_user_id / created_by) keep resolving — this
matches the project's append-only-audit / data-preservation stance. The user
disappears from the admin list (list_all filters deleted_at IS NULL) and the
email is freed for reuse (partial unique index WHERE deleted_at IS NULL).

Guards (mirror DeactivateUser): cannot delete self; cannot delete a built-in
system account; cannot delete the last admin / last super_admin (FOR UPDATE
serialised, F-004). Idempotent: an already soft-deleted user emits no event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import DeleteUserCommand
from auth_service.application.ports import (
    EventOutbox,
    RedisLockoutStore,
    SessionRepository,
    UserRepository,
)
from auth_service.application.use_cases.check_min_admins import (
    CheckMinAdmins,
    CheckMinSuperAdmins,
)
from auth_service.domain.errors import (
    SelfActionForbiddenError,
    SystemAccountProtectedError,
    UserNotFoundError,
)
from auth_service.domain.events import SessionRevokedAll, UserDeleted

_SYSTEM_EMAIL_SUFFIX = "@system.lotsman"


@dataclass(slots=True)
class DeleteUser:
    user_repo: UserRepository
    session_repo: SessionRepository
    lockout_store: RedisLockoutStore
    outbox: EventOutbox

    async def execute(self, *, cmd: DeleteUserCommand) -> None:
        if cmd.actor_id == cmd.target_user_id:
            raise SelfActionForbiddenError("Cannot delete your own account")

        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        if user.email.endswith(_SYSTEM_EMAIL_SUFFIX):
            raise SystemAccountProtectedError()

        # Idempotent: already soft-deleted → nothing to do.
        if user.deleted_at is not None:
            return

        # Guard: last-admin (counts active admins; deleting removes one).
        guard = CheckMinAdmins(user_repo=self.user_repo, outbox=self.outbox)
        await guard.guard(
            actor_id=cmd.actor_id,
            target_user_id=cmd.target_user_id,
            target_is_admin=(user.role == "admin"),
            operation="deactivate",
        )

        # Guard: last-super_admin.
        if user.role == "super_admin":
            super_guard = CheckMinSuperAdmins(
                user_repo=self.user_repo, outbox=self.outbox
            )
            await super_guard.guard(
                actor_id=cmd.actor_id,
                target_user_id=cmd.target_user_id,
                operation="deactivate",
            )

        # Soft-delete: hide from list + free email, keep row for audit/refs.
        user.is_active = False
        user.deleted_at = datetime.now(tz=UTC)
        await self.user_repo.update(user)

        await self.lockout_store.set_locked(cmd.target_user_id)
        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)

        await self.outbox.publish(
            UserDeleted(actor_id=cmd.actor_id, user_id=cmd.target_user_id).as_envelope()
        )
        if revoked_count > 0:
            await self.outbox.publish(
                SessionRevokedAll(
                    actor_id=cmd.actor_id,
                    target_user_id=cmd.target_user_id,
                    revoked_count=revoked_count,
                ).as_envelope()
            )
