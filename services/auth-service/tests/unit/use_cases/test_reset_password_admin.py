# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ResetPasswordAdmin use case (US-7)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ResetPasswordAdminCommand
from auth_service.application.use_cases.reset_password_admin import ResetPasswordAdmin
from auth_service.domain.errors import DeactivatedUserOperationError, UserNotFoundError

from .conftest import (
    FakeEventOutbox,
    FakePasswordHasher,
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
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> ResetPasswordAdmin:
    return ResetPasswordAdmin(
        user_repo=user_repo,
        session_repo=session_repo,
        hasher=FakePasswordHasher(),
        outbox=outbox,
    )


@pytest.mark.asyncio
async def test_reset_generates_oob_otp(
    use_case: ResetPasswordAdmin,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)

    result = await use_case.execute(
        cmd=ResetPasswordAdminCommand(actor_id=actor_id, target_user_id=target.id)
    )

    assert len(result.oob_otp) > 0
    stored = await user_repo.get_by_id(target.id)
    assert stored is not None
    assert stored.password_hash == f"HASH:{result.oob_otp}"
    assert stored.must_change_password is True
    assert "auth.user.password_reset.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_reset_revokes_all_sessions(
    use_case: ResetPasswordAdmin,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)

    s1 = make_session(user_id=target.id, refresh_hash="h1")
    s2 = make_session(user_id=target.id, refresh_hash="h2")
    await session_repo.add(s1)
    await session_repo.add(s2)

    await use_case.execute(
        cmd=ResetPasswordAdminCommand(actor_id=actor_id, target_user_id=target.id)
    )

    for s in [s1, s2]:
        stored = await session_repo.get_by_id(s.id)
        assert stored is not None
        assert stored.revoked_at is not None


@pytest.mark.asyncio
async def test_unknown_user_raises(use_case: ResetPasswordAdmin) -> None:
    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=ResetPasswordAdminCommand(actor_id=uuid.uuid4(), target_user_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_deactivated_user_raises(
    use_case: ResetPasswordAdmin,
    user_repo: FakeUserRepository,
) -> None:
    target = make_user(is_active=False)
    await user_repo.add(target)

    with pytest.raises(DeactivatedUserOperationError):
        await use_case.execute(
            cmd=ResetPasswordAdminCommand(actor_id=uuid.uuid4(), target_user_id=target.id)
        )


@pytest.mark.asyncio
async def test_oob_otp_is_unique_per_call(
    use_case: ResetPasswordAdmin,
    user_repo: FakeUserRepository,
) -> None:
    actor_id = uuid.uuid4()
    t1 = make_user(email="u1@example.com")
    t2 = make_user(email="u2@example.com")
    await user_repo.add(t1)
    await user_repo.add(t2)

    r1 = await use_case.execute(
        cmd=ResetPasswordAdminCommand(actor_id=actor_id, target_user_id=t1.id)
    )
    r2 = await use_case.execute(
        cmd=ResetPasswordAdminCommand(actor_id=actor_id, target_user_id=t2.id)
    )
    assert r1.oob_otp != r2.oob_otp
