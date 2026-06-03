# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for LockoutUserAdmin and UnlockUserAdmin use cases."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import LockoutUserAdminCommand, UnlockUserAdminCommand
from auth_service.application.use_cases.lockout_user_admin import LockoutUserAdmin
from auth_service.application.use_cases.unlock_user_admin import UnlockUserAdmin
from auth_service.domain.errors import UserNotFoundError

from .conftest import (
    FakeEventOutbox,
    FakeRedisLockoutStore,
    FakeSessionRepository,
    FakeUserRepository,
    make_session,
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
def lockout_uc(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> LockoutUserAdmin:
    return LockoutUserAdmin(
        user_repo=user_repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )


@pytest.fixture()
def unlock_uc(
    user_repo: FakeUserRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> UnlockUserAdmin:
    return UnlockUserAdmin(
        user_repo=user_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )


# ---------------------------------------------------------------------------
# LockoutUserAdmin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lockout_sets_redis_flag_and_revokes_sessions(
    lockout_uc: LockoutUserAdmin,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user(email="target@example.com")
    await user_repo.add(target)

    s = make_session(user_id=target.id)
    await session_repo.add(s)

    await lockout_uc.execute(
        cmd=LockoutUserAdminCommand(actor_id=actor_id, target_user_id=target.id)
    )

    assert await lockout_store.is_locked(target.id) is True
    stored_session = await session_repo.get_by_id(s.id)
    assert stored_session is not None
    assert stored_session.revoked_at is not None

    event_types = outbox.event_types()
    assert "auth.user.locked.v1" in event_types
    assert "auth.session.revoked_all.v1" in event_types


@pytest.mark.asyncio
async def test_lockout_unknown_user_raises(lockout_uc: LockoutUserAdmin) -> None:
    with pytest.raises(UserNotFoundError):
        await lockout_uc.execute(
            cmd=LockoutUserAdminCommand(actor_id=uuid.uuid4(), target_user_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_lockout_idempotent_no_duplicate_event(
    lockout_uc: LockoutUserAdmin,
    user_repo: FakeUserRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)
    await lockout_store.set_locked(target.id)  # pre-locked

    await lockout_uc.execute(
        cmd=LockoutUserAdminCommand(actor_id=actor_id, target_user_id=target.id)
    )

    # No new event because already locked
    assert outbox.events == []


# ---------------------------------------------------------------------------
# UnlockUserAdmin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_removes_redis_flag_and_emits_event(
    unlock_uc: UnlockUserAdmin,
    user_repo: FakeUserRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)
    await lockout_store.set_locked(target.id)

    await unlock_uc.execute(cmd=UnlockUserAdminCommand(actor_id=actor_id, target_user_id=target.id))

    assert await lockout_store.is_locked(target.id) is False
    assert "auth.account.unlocked.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_unlock_unknown_user_raises(unlock_uc: UnlockUserAdmin) -> None:
    with pytest.raises(UserNotFoundError):
        await unlock_uc.execute(
            cmd=UnlockUserAdminCommand(actor_id=uuid.uuid4(), target_user_id=uuid.uuid4())
        )
