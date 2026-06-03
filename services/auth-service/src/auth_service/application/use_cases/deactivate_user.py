# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""DeactivateUser use case — US-18.

Reversibly disables a user (is_active=False ONLY — does NOT set deleted_at, so
the user stays visible in the admin list and can be re-activated). Revokes all
sessions, sets Redis lockout flag, emits UserDeactivated. Permanent removal is a
separate DeleteUser use case (sets deleted_at → soft-delete).

Guard: cannot deactivate self; cannot deactivate a built-in system account;
cannot deactivate last admin; cannot deactivate last super_admin.
Idempotent: already-deactivated users emit no duplicate event.

MIN_ADMINS / MIN_SUPER_ADMINS race condition note (F-004 / ADR-0004 §6):
    count_active_by_role() uses SELECT ... FOR UPDATE so concurrent
    deactivate/demote operations on the role rows are serialised at
    the database level, making the MIN_* invariants airtight.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import DeactivateUserCommand
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
from auth_service.domain.events import SessionRevokedAll, UserDeactivated

_SYSTEM_EMAIL_SUFFIX = "@system.lotsman"


@dataclass(slots=True)
class DeactivateUser:
    user_repo: UserRepository
    session_repo: SessionRepository
    lockout_store: RedisLockoutStore
    outbox: EventOutbox

    async def execute(self, *, cmd: DeactivateUserCommand) -> None:
        if cmd.actor_id == cmd.target_user_id:
            raise SelfActionForbiddenError("Cannot deactivate your own account")

        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        if user.email.endswith(_SYSTEM_EMAIL_SUFFIX):
            raise SystemAccountProtectedError()

        # Idempotent
        if not user.is_active:
            return

        # Guard: last-admin check (emits auth.policy.violation.v1 before raising).
        # Uses CheckMinAdmins which calls count_active_by_role() with FOR UPDATE
        # to prevent the TOCTOU race (F-004).
        guard = CheckMinAdmins(user_repo=self.user_repo, outbox=self.outbox)
        await guard.guard(
            actor_id=cmd.actor_id,
            target_user_id=cmd.target_user_id,
            target_is_admin=(user.role == "admin"),
            operation="deactivate",
        )

        # Guard: last-super_admin check — cannot deactivate the last super_admin.
        if user.role == "super_admin":
            super_guard = CheckMinSuperAdmins(user_repo=self.user_repo, outbox=self.outbox)
            await super_guard.guard(
                actor_id=cmd.actor_id,
                target_user_id=cmd.target_user_id,
                operation="deactivate",
            )

        # Deactivate — reversible: flip is_active only, keep deleted_at NULL so
        # the user remains listed and can be re-activated. (DeleteUser sets
        # deleted_at for permanent soft-removal.)
        user.is_active = False
        await self.user_repo.update(user)

        # Set instant lockout flag
        await self.lockout_store.set_locked(cmd.target_user_id)

        # Revoke sessions
        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)

        # Emit events
        await self.outbox.publish(
            UserDeactivated(actor_id=cmd.actor_id, user_id=cmd.target_user_id).as_envelope()
        )
        if revoked_count > 0:
            await self.outbox.publish(
                SessionRevokedAll(
                    actor_id=cmd.actor_id,
                    target_user_id=cmd.target_user_id,
                    revoked_count=revoked_count,
                ).as_envelope()
            )
