# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for DeactivateUser use case (US-18)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import DeactivateUserCommand
from auth_service.application.use_cases.deactivate_user import DeactivateUser
from auth_service.domain.errors import (
    MinAdminsViolationError,
    SelfActionForbiddenError,
    UserNotFoundError,
)

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
def use_case(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> DeactivateUser:
    return DeactivateUser(
        user_repo=user_repo,
        session_repo=session_repo,
        lockout_store=lockout_store,
        outbox=outbox,
    )


@pytest.mark.asyncio
async def test_deactivates_user(
    use_case: DeactivateUser,
    user_repo: FakeUserRepository,
    lockout_store: FakeRedisLockoutStore,
    outbox: FakeEventOutbox,
) -> None:
    actor = make_user(role="admin")
    target = make_user(role="editor")
    # Add a second admin so last-admin guard passes for the actor
    await user_repo.add(actor)
    await user_repo.add(target)

    await use_case.execute(cmd=DeactivateUserCommand(actor_id=actor.id, target_user_id=target.id))

    stored = await user_repo.get_by_id(target.id)
    assert stored is not None
    assert stored.is_active is False
    # Deactivate is now REVERSIBLE — it must NOT soft-delete (deleted_at stays
    # NULL so the user remains listed and can be re-activated). Permanent removal
    # is DeleteUser, which sets deleted_at.
    assert stored.deleted_at is None
    assert await lockout_store.is_locked(target.id) is True
    assert "auth.user.deactivated.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_revokes_active_sessions(
    use_case: DeactivateUser,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor = make_user(role="admin")
    target = make_user(role="editor")
    await user_repo.add(actor)
    await user_repo.add(target)

    s = make_session(user_id=target.id)
    await session_repo.add(s)

    await use_case.execute(cmd=DeactivateUserCommand(actor_id=actor.id, target_user_id=target.id))

    # Session should now be revoked
    stored_session = await session_repo.get_by_id(s.id)
    assert stored_session is not None
    assert stored_session.revoked_at is not None
    # SessionRevokedAll event should be present
    assert "auth.session.revoked_all.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_self_deactivate_raises(
    use_case: DeactivateUser, user_repo: FakeUserRepository
) -> None:
    actor = make_user(role="admin")
    await user_repo.add(actor)

    with pytest.raises(SelfActionForbiddenError):
        await use_case.execute(
            cmd=DeactivateUserCommand(actor_id=actor.id, target_user_id=actor.id)
        )


@pytest.mark.asyncio
async def test_unknown_target_raises(
    use_case: DeactivateUser, user_repo: FakeUserRepository
) -> None:
    actor = make_user(role="admin")
    await user_repo.add(actor)

    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=DeactivateUserCommand(actor_id=actor.id, target_user_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_last_admin_guard(
    use_case: DeactivateUser,
    user_repo: FakeUserRepository,
) -> None:
    actor = make_user(role="admin")
    target = make_user(role="admin")  # only two admins
    await user_repo.add(actor)
    await user_repo.add(target)

    # Deactivate target would leave only actor as admin — but we're targeting the *only other* admin
    # The fake counts both, so deactivating one still leaves one (actor). This should pass.
    # To trigger LastAdminError, we need to deactivate the ONLY admin:
    single_admin = make_user(role="admin", email="solo@example.com")
    other_actor_id = uuid.uuid4()  # some non-admin actor
    # Reset repo to only have the single admin
    single_repo = FakeUserRepository()
    await single_repo.add(single_admin)

    use_case_single = DeactivateUser(
        user_repo=single_repo,
        session_repo=FakeSessionRepository(),
        lockout_store=FakeRedisLockoutStore(),
        outbox=FakeEventOutbox(),
    )

    with pytest.raises(MinAdminsViolationError):
        await use_case_single.execute(
            cmd=DeactivateUserCommand(actor_id=other_actor_id, target_user_id=single_admin.id)
        )


@pytest.mark.asyncio
async def test_idempotent_if_already_deactivated(
    use_case: DeactivateUser,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor = make_user(role="admin")
    target = make_user(role="editor", is_active=False)
    await user_repo.add(actor)
    await user_repo.add(target)

    await use_case.execute(cmd=DeactivateUserCommand(actor_id=actor.id, target_user_id=target.id))

    # No event should be emitted on no-op
    assert outbox.events == []
