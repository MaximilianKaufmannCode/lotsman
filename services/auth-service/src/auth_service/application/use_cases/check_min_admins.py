# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CheckMinRoleHolders helper — US-12 / Phase 1 super_admin extension.

Called by DeactivateUser and ChangeRole before they mutate user state.
If the operation would leave 0 active holders of the given role →
raises the appropriate violation error and emits auth.policy.violation.v1
in the SAME transaction.

This is a helper (not a top-level use case with its own execute() signature)
because it has no side-effect of its own except emitting the violation event.

MIN_ADMINS race condition (F-004 / ADR-0004 §6):
    count_active_by_role() must use SELECT ... FOR UPDATE over the target-role rows
    so that two concurrent demote/deactivate transactions cannot both read
    count=2, both pass the guard, and both commit, leaving 0 role holders.
    The infrastructure implementation (SqlaUserRepository.count_active_by_role)
    is required to add ``WITH FOR UPDATE`` to the query.

Backward-compatible aliases:
    CheckMinAdmins  — wraps CheckMinRoleHolders with role='admin'
    CheckMinSuperAdmins — wraps CheckMinRoleHolders with role='super_admin'
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from auth_service.application.ports import EventOutbox, UserRepository
from auth_service.domain.errors import MinAdminsViolationError, MinSuperAdminsViolationError
from auth_service.domain.events import PolicyViolationAttempted

_Operation = Literal["deactivate", "role_change"]

_ROLE_POLICY = {
    "admin": "MIN_ADMINS",
    "super_admin": "MIN_SUPER_ADMINS",
}


@dataclass(slots=True)
class CheckMinRoleHolders:
    """Guard: at least 1 active holder of *role* must remain after the proposed operation.

    Parameterised by role so a single implementation covers both
    the MIN_ADMINS and MIN_SUPER_ADMINS invariants.
    """

    user_repo: UserRepository
    outbox: EventOutbox

    async def guard(
        self,
        *,
        role: str,
        actor_id: uuid.UUID,
        target_user_id: uuid.UUID,
        operation: _Operation = "deactivate",
    ) -> None:
        """Raise the role-appropriate violation error if the operation would leave 0 active holders.

        Emits ``auth.policy.violation.v1`` BEFORE raising so the event is committed
        in the same transaction as the guard check (caller must be inside a tx).

        Args:
            role: the role being vacated ('admin' or 'super_admin').
            actor_id: UUID of the actor attempting the operation.
            target_user_id: UUID of the user being operated on.
            operation: "deactivate" | "role_change" — recorded in the event payload.
        """
        policy = _ROLE_POLICY.get(role, f"MIN_{role.upper()}")
        active_count = await self.user_repo.count_active_by_role(role)

        if active_count <= 1:
            await self.outbox.publish(
                PolicyViolationAttempted(
                    actor_id=actor_id,
                    policy=policy,
                    target_user_id=target_user_id,
                    operation=operation,
                ).as_envelope()
            )
            if role == "super_admin":
                raise MinSuperAdminsViolationError()
            raise MinAdminsViolationError()


@dataclass(slots=True)
class CheckMinAdmins:
    """Backward-compatible alias: guard that at least 1 active admin remains.

    Wraps CheckMinRoleHolders with role='admin'.

    NOTE: make admin-create MUST NOT create super_admin users — the
    bootstrap_admin use case hard-codes role='admin'. This guard is
    intentionally separated from CheckMinSuperAdmins.
    """

    user_repo: UserRepository
    outbox: EventOutbox

    async def guard(
        self,
        *,
        actor_id: uuid.UUID,
        target_user_id: uuid.UUID,
        target_is_admin: bool,
        operation: _Operation = "deactivate",
    ) -> None:
        """Raise MinAdminsViolationError if operating on the last admin.

        Args:
            target_is_admin: True if the operation would reduce the admin count
                (i.e. target currently holds role='admin' for deactivate, or
                currently holds 'admin' and is being demoted for role_change).
            operation: "deactivate" | "role_change" — recorded in the event payload.
        """
        if not target_is_admin:
            return

        inner = CheckMinRoleHolders(user_repo=self.user_repo, outbox=self.outbox)
        await inner.guard(
            role="admin",
            actor_id=actor_id,
            target_user_id=target_user_id,
            operation=operation,
        )


@dataclass(slots=True)
class CheckMinSuperAdmins:
    """Guard: at least 1 active super_admin must remain after the proposed operation.

    Wraps CheckMinRoleHolders with role='super_admin'.
    """

    user_repo: UserRepository
    outbox: EventOutbox

    async def guard(
        self,
        *,
        actor_id: uuid.UUID,
        target_user_id: uuid.UUID,
        operation: _Operation = "deactivate",
    ) -> None:
        """Raise MinSuperAdminsViolationError if operating on the last super_admin."""
        inner = CheckMinRoleHolders(user_repo=self.user_repo, outbox=self.outbox)
        await inner.guard(
            role="super_admin",
            actor_id=actor_id,
            target_user_id=target_user_id,
            operation=operation,
        )
