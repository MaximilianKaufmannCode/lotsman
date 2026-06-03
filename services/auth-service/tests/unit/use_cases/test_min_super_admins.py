# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for CheckMinSuperAdmins — ADR-0006 Phase 1.

Verifies that the last super_admin cannot be deactivated or demoted,
and that ChangeRole / DeactivateUser wire the guard correctly.
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.use_cases.check_min_admins import CheckMinSuperAdmins
from auth_service.domain.errors import MinSuperAdminsViolationError

from .conftest import (
    FakeEventOutbox,
    FakeRedisLockoutStore,
    FakeSessionRepository,
    FakeUserRepository,
    make_user,
)

# ---------------------------------------------------------------------------
# CheckMinSuperAdmins unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_blocks_last_super_admin_deactivate() -> None:
    """Cannot deactivate the last super_admin — guard raises MinSuperAdminsViolationError."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    super_admin = make_user(role="super_admin")
    await repo.add(super_admin)

    guard = CheckMinSuperAdmins(user_repo=repo, outbox=outbox)
    with pytest.raises(MinSuperAdminsViolationError):
        await guard.guard(
            actor_id=super_admin.id,
            target_user_id=super_admin.id,
            operation="deactivate",
        )

    # Policy violation event emitted with MIN_SUPER_ADMINS code.
    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_SUPER_ADMINS"
    assert violation_env.payload["operation"] == "deactivate"
    assert violation_env.payload["target_user_id"] == str(super_admin.id)


@pytest.mark.asyncio
async def test_guard_blocks_last_super_admin_role_change() -> None:
    """Cannot demote the last super_admin — guard raises MinSuperAdminsViolationError."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    super_admin = make_user(role="super_admin")
    await repo.add(super_admin)

    guard = CheckMinSuperAdmins(user_repo=repo, outbox=outbox)
    with pytest.raises(MinSuperAdminsViolationError):
        await guard.guard(
            actor_id=super_admin.id,
            target_user_id=super_admin.id,
            operation="role_change",
        )

    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_SUPER_ADMINS"
    assert violation_env.payload["operation"] == "role_change"


@pytest.mark.asyncio
async def test_guard_allows_when_two_super_admins() -> None:
    """Guard allows deactivation when ≥2 active super_admins exist."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    sa_a = make_user(role="super_admin", email="alice@org.local")
    sa_b = make_user(role="super_admin", email="bob@org.local")
    await repo.add(sa_a)
    await repo.add(sa_b)

    guard = CheckMinSuperAdmins(user_repo=repo, outbox=outbox)
    # Should not raise.
    await guard.guard(
        actor_id=sa_a.id,
        target_user_id=sa_b.id,
        operation="deactivate",
    )
    assert len(outbox.events) == 0


@pytest.mark.asyncio
async def test_guard_does_not_affect_admin_count() -> None:
    """MIN_SUPER_ADMINS guard ignores regular admin accounts."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    # 2 admins but only 1 super_admin — guard must still block
    super_admin = make_user(role="super_admin", email="super@org.local")
    admin_a = make_user(role="admin", email="admina@org.local")
    admin_b = make_user(role="admin", email="adminb@org.local")
    await repo.add(super_admin)
    await repo.add(admin_a)
    await repo.add(admin_b)

    guard = CheckMinSuperAdmins(user_repo=repo, outbox=outbox)
    with pytest.raises(MinSuperAdminsViolationError):
        await guard.guard(
            actor_id=admin_a.id,
            target_user_id=super_admin.id,
            operation="deactivate",
        )

    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_SUPER_ADMINS"


# ---------------------------------------------------------------------------
# DeactivateUser wiring for super_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_user_blocks_last_super_admin() -> None:
    """DeactivateUser raises MinSuperAdminsViolationError when demoting last super_admin."""
    from auth_service.application.dto import DeactivateUserCommand
    from auth_service.application.use_cases.deactivate_user import DeactivateUser

    repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    lockout_store = FakeRedisLockoutStore()
    outbox = FakeEventOutbox()

    super_admin = make_user(role="super_admin")
    await repo.add(super_admin)

    use_case = DeactivateUser(
        user_repo=repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )
    actor_id = uuid.uuid4()
    with pytest.raises(MinSuperAdminsViolationError):
        await use_case.execute(
            cmd=DeactivateUserCommand(
                target_user_id=super_admin.id,
                actor_id=actor_id,
            )
        )

    # Policy violation event emitted.
    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_SUPER_ADMINS"
    assert violation_env.payload["operation"] == "deactivate"


@pytest.mark.asyncio
async def test_deactivate_user_allows_super_admin_when_two_exist() -> None:
    """DeactivateUser succeeds when ≥2 super_admins exist."""
    from auth_service.application.dto import DeactivateUserCommand
    from auth_service.application.use_cases.deactivate_user import DeactivateUser

    repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    lockout_store = FakeRedisLockoutStore()
    outbox = FakeEventOutbox()

    sa_a = make_user(role="super_admin", email="alice@org.local")
    sa_b = make_user(role="super_admin", email="bob@org.local")
    await repo.add(sa_a)
    await repo.add(sa_b)

    use_case = DeactivateUser(
        user_repo=repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )
    # Should not raise — sa_a deactivates sa_b, sa_a remains.
    await use_case.execute(
        cmd=DeactivateUserCommand(
            target_user_id=sa_b.id,
            actor_id=sa_a.id,
        )
    )

    updated = await repo.get_by_id(sa_b.id)
    assert updated is not None
    assert updated.is_active is False


# ---------------------------------------------------------------------------
# ChangeRole wiring for super_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_role_blocks_demotion_of_last_super_admin() -> None:
    """ChangeRole raises MinSuperAdminsViolationError when demoting last super_admin."""
    from auth_service.application.dto import ChangeRoleCommand
    from auth_service.application.use_cases.change_role import ChangeRole

    repo = FakeUserRepository()
    outbox = FakeEventOutbox()

    super_admin = make_user(role="super_admin")
    await repo.add(super_admin)

    use_case = ChangeRole(user_repo=repo, outbox=outbox)
    with pytest.raises(MinSuperAdminsViolationError):
        await use_case.execute(
            cmd=ChangeRoleCommand(
                target_user_id=super_admin.id,
                new_role="admin",
                actor_id=uuid.uuid4(),
            )
        )

    assert "auth.policy.violation.v1" in outbox.event_types()
    violation_env = next(e for e in outbox.events if e.type == "auth.policy.violation.v1")
    assert violation_env.payload["policy"] == "MIN_SUPER_ADMINS"
    assert violation_env.payload["operation"] == "role_change"


@pytest.mark.asyncio
async def test_change_role_allows_super_admin_to_super_admin_noop() -> None:
    """ChangeRole is a no-op when new_role == current_role, no events emitted."""
    from auth_service.application.dto import ChangeRoleCommand
    from auth_service.application.use_cases.change_role import ChangeRole

    repo = FakeUserRepository()
    outbox = FakeEventOutbox()

    super_admin = make_user(role="super_admin")
    await repo.add(super_admin)

    use_case = ChangeRole(user_repo=repo, outbox=outbox)
    result = await use_case.execute(
        cmd=ChangeRoleCommand(
            target_user_id=super_admin.id,
            new_role="super_admin",
            actor_id=uuid.uuid4(),
        )
    )
    assert result.role == "super_admin"
    assert len(outbox.events) == 0
