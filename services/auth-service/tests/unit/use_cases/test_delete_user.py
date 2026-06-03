# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for DeleteUser use case (permanent soft-delete)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from auth_service.application.dto import DeleteUserCommand
from auth_service.application.use_cases.delete_user import DeleteUser
from auth_service.domain.errors import (
    MinAdminsViolationError,
    SelfActionForbiddenError,
    SystemAccountProtectedError,
    UserNotFoundError,
)

from .conftest import (
    FakeEventOutbox,
    FakeRedisLockoutStore,
    FakeSessionRepository,
    FakeUserRepository,
    make_user,
)


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def session_repo() -> FakeSessionRepository:
    return FakeSessionRepository()


@pytest.fixture()
def lockout_store() -> FakeRedisLockoutStore:
    return FakeRedisLockoutStore()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> DeleteUser:
    return DeleteUser(
        user_repo=user_repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )


@pytest.mark.asyncio
async def test_soft_deletes_user(
    use_case: DeleteUser,
    user_repo: FakeUserRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> None:
    actor = make_user(role="admin", email="a@example.com")
    target = make_user(role="editor", email="t@example.com")
    await user_repo.add(actor)
    await user_repo.add(target)

    await use_case.execute(cmd=DeleteUserCommand(actor_id=actor.id, target_user_id=target.id))

    stored = await user_repo.get_by_id(target.id)
    assert stored is not None
    assert stored.is_active is False
    assert stored.deleted_at is not None  # soft-deleted (hidden + email freed)
    assert await lockout_store.is_locked(target.id) is True
    assert "auth.user.deleted.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_self_delete_raises(use_case: DeleteUser, user_repo: FakeUserRepository) -> None:
    actor = make_user(role="admin", email="a@example.com")
    await user_repo.add(actor)
    with pytest.raises(SelfActionForbiddenError):
        await use_case.execute(cmd=DeleteUserCommand(actor_id=actor.id, target_user_id=actor.id))


@pytest.mark.asyncio
async def test_system_account_protected(
    use_case: DeleteUser, user_repo: FakeUserRepository
) -> None:
    actor = make_user(role="admin", email="a@example.com")
    system = make_user(role="viewer", email="outbox-dispatcher@system.lotsman", is_active=False)
    await user_repo.add(actor)
    await user_repo.add(system)
    with pytest.raises(SystemAccountProtectedError):
        await use_case.execute(cmd=DeleteUserCommand(actor_id=actor.id, target_user_id=system.id))


@pytest.mark.asyncio
async def test_unknown_target_raises(
    use_case: DeleteUser, user_repo: FakeUserRepository
) -> None:
    actor = make_user(role="admin", email="a@example.com")
    await user_repo.add(actor)

    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=DeleteUserCommand(actor_id=actor.id, target_user_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_last_admin_guard(
    use_case: DeleteUser, user_repo: FakeUserRepository
) -> None:
    # Exactly one admin in the system → deleting them must be blocked. Actor is a
    # non-admin so it isn't itself the admin being counted (RBAC is enforced at the
    # API layer; the use case only enforces the MIN_ADMINS invariant).
    editor_actor = make_user(role="editor", email="ed@example.com")
    lone_admin = make_user(role="admin", email="lone@example.com")
    await user_repo.add(editor_actor)
    await user_repo.add(lone_admin)

    with pytest.raises(MinAdminsViolationError):
        await use_case.execute(
            cmd=DeleteUserCommand(actor_id=editor_actor.id, target_user_id=lone_admin.id)
        )


@pytest.mark.asyncio
async def test_idempotent_if_already_deleted(
    use_case: DeleteUser, user_repo: FakeUserRepository, outbox: FakeEventOutbox
) -> None:
    actor = make_user(role="admin", email="a@example.com")
    target = make_user(role="editor", email="t@example.com", is_active=False)
    target.deleted_at = datetime.now(tz=UTC)
    await user_repo.add(actor)
    await user_repo.add(target)

    await use_case.execute(cmd=DeleteUserCommand(actor_id=actor.id, target_user_id=target.id))
    assert "auth.user.deleted.v1" not in outbox.event_types()
