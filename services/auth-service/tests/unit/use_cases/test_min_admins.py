# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for CheckMinAdmins and its integration with DeactivateUser/ChangeRole.

US-12: Backend blocks deactivate/role-change of last admin.
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.use_cases.check_min_admins import CheckMinAdmins
from auth_service.domain.errors import MinAdminsViolationError

from .conftest import (
    FakeEventOutbox,
    FakeRedisLockoutStore,
    FakeSessionRepository,
    FakeUserRepository,
    make_user,
)


@pytest.mark.asyncio
async def test_guard_blocks_last_admin() -> None:
    """Cannot demote/deactivate the last admin — guard raises MinAdminsViolationError."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    admin = make_user(role="admin")
    await repo.add(admin)

    guard = CheckMinAdmins(user_repo=repo, outbox=outbox)
    with pytest.raises(MinAdminsViolationError):
        await guard.guard(
            actor_id=admin.id,
            target_user_id=admin.id,
            target_is_admin=True,
            operation="deactivate",
        )

    # Policy violation event emitted.
    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_ADMINS"
    assert violation_env.payload["operation"] == "deactivate"
    assert violation_env.payload["target_user_id"] == str(admin.id)


@pytest.mark.asyncio
async def test_guard_blocks_role_change() -> None:
    """Guard emits auth.policy.violation.v1 with operation=role_change."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    admin = make_user(role="admin")
    await repo.add(admin)

    guard = CheckMinAdmins(user_repo=repo, outbox=outbox)
    with pytest.raises(MinAdminsViolationError):
        await guard.guard(
            actor_id=admin.id,
            target_user_id=admin.id,
            target_is_admin=True,
            operation="role_change",
        )

    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["operation"] == "role_change"


@pytest.mark.asyncio
async def test_guard_allows_when_two_admins() -> None:
    """Guard allows deactivation when ≥2 active admins exist."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    admin_a = make_user(role="admin", email="alice@org.local")
    admin_b = make_user(role="admin", email="bob@org.local")
    await repo.add(admin_a)
    await repo.add(admin_b)

    guard = CheckMinAdmins(user_repo=repo, outbox=outbox)
    # Should not raise.
    await guard.guard(
        actor_id=admin_a.id,
        target_user_id=admin_b.id,
        target_is_admin=True,
        operation="deactivate",
    )
    assert len(outbox.events) == 0


@pytest.mark.asyncio
async def test_guard_skips_non_admin_target() -> None:
    """Guard does nothing if target is not an admin."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    admin = make_user(role="admin")
    editor = make_user(role="editor")
    await repo.add(admin)
    await repo.add(editor)

    guard = CheckMinAdmins(user_repo=repo, outbox=outbox)
    # Should not raise — editor doesn't count as admin.
    await guard.guard(
        actor_id=admin.id,
        target_user_id=editor.id,
        target_is_admin=False,
        operation="deactivate",
    )
    assert len(outbox.events) == 0


@pytest.mark.asyncio
async def test_deactivate_user_wires_check_min_admins_and_emits_violation() -> None:
    """DeactivateUser now calls CheckMinAdmins, which emits auth.policy.violation.v1.

    Previously deactivate_user.py raised LastAdminError directly without emitting
    the policy event. Blocker 2 fix: it now calls CheckMinAdmins.guard() which
    emits the event before raising MinAdminsViolationError.
    """
    from auth_service.application.dto import DeactivateUserCommand
    from auth_service.application.use_cases.deactivate_user import DeactivateUser
    from auth_service.domain.errors import MinAdminsViolationError

    repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    lockout_store = FakeRedisLockoutStore()
    outbox = FakeEventOutbox()

    admin = make_user(role="admin")
    await repo.add(admin)

    use_case = DeactivateUser(
        user_repo=repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )
    other_admin_id = uuid.uuid4()
    with pytest.raises(MinAdminsViolationError):
        await use_case.execute(
            cmd=DeactivateUserCommand(
                target_user_id=admin.id,
                actor_id=other_admin_id,
            )
        )

    # The policy violation event must have been emitted.
    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_ADMINS"
    assert violation_env.payload["operation"] == "deactivate"


@pytest.mark.asyncio
async def test_change_role_wires_check_min_admins_and_emits_violation() -> None:
    """ChangeRole now calls CheckMinAdmins when demoting an admin, emitting the violation event."""
    from auth_service.application.dto import ChangeRoleCommand
    from auth_service.application.use_cases.change_role import ChangeRole
    from auth_service.domain.errors import MinAdminsViolationError

    repo = FakeUserRepository()
    outbox = FakeEventOutbox()

    admin = make_user(role="admin")
    await repo.add(admin)

    use_case = ChangeRole(user_repo=repo, outbox=outbox)
    with pytest.raises(MinAdminsViolationError):
        await use_case.execute(
            cmd=ChangeRoleCommand(
                target_user_id=admin.id,
                new_role="editor",
                actor_id=uuid.uuid4(),
            )
        )

    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_ADMINS"
    assert violation_env.payload["operation"] == "role_change"
