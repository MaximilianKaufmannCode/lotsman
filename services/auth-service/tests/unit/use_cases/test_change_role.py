# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ChangeRole use case (US-19)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ChangeRoleCommand
from auth_service.application.use_cases.change_role import ChangeRole
from auth_service.domain.errors import MinAdminsViolationError, UserNotFoundError

from .conftest import FakeEventOutbox, FakeUserRepository, make_user


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(user_repo: FakeUserRepository, outbox: FakeEventOutbox) -> ChangeRole:
    return ChangeRole(user_repo=user_repo, outbox=outbox)


@pytest.mark.asyncio
async def test_changes_role_and_emits_event(
    use_case: ChangeRole,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user(role="editor")
    await user_repo.add(target)

    result = await use_case.execute(
        cmd=ChangeRoleCommand(target_user_id=target.id, new_role="viewer", actor_id=actor_id)
    )

    assert result.role == "viewer"
    stored = await user_repo.get_by_id(target.id)
    assert stored is not None
    assert stored.role == "viewer"
    assert "auth.user.role_changed.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_no_op_if_same_role(
    use_case: ChangeRole,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user(role="editor")
    await user_repo.add(target)

    result = await use_case.execute(
        cmd=ChangeRoleCommand(target_user_id=target.id, new_role="editor", actor_id=actor_id)
    )

    assert result.role == "editor"
    # No event when role is unchanged
    assert outbox.events == []


@pytest.mark.asyncio
async def test_demote_last_admin_raises(
    use_case: ChangeRole,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    """Last admin demotion now raises MinAdminsViolationError (via CheckMinAdmins) + emits event."""
    actor_id = uuid.uuid4()
    sole_admin = make_user(role="admin")
    await user_repo.add(sole_admin)

    with pytest.raises(MinAdminsViolationError):
        await use_case.execute(
            cmd=ChangeRoleCommand(
                target_user_id=sole_admin.id, new_role="editor", actor_id=actor_id
            )
        )

    # CheckMinAdmins emits the policy violation event before raising.
    assert "auth.policy.violation.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_demote_non_last_admin_succeeds(
    use_case: ChangeRole,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    admin1 = make_user(role="admin", email="a1@example.com")
    admin2 = make_user(role="admin", email="a2@example.com")
    await user_repo.add(admin1)
    await user_repo.add(admin2)

    result = await use_case.execute(
        cmd=ChangeRoleCommand(target_user_id=admin1.id, new_role="viewer", actor_id=actor_id)
    )

    assert result.role == "viewer"
    assert "auth.user.role_changed.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_unknown_user_raises(use_case: ChangeRole) -> None:
    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=ChangeRoleCommand(
                target_user_id=uuid.uuid4(),
                new_role="editor",
                actor_id=uuid.uuid4(),
            )
        )
