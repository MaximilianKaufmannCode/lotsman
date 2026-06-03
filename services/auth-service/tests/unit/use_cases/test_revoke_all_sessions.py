# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RevokeAllSessions use case (US-15)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import RevokeAllSessionsCommand
from auth_service.application.use_cases.revoke_all_sessions import RevokeAllSessions
from auth_service.domain.errors import UserNotFoundError

from .conftest import (
    FakeEventOutbox,
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
) -> RevokeAllSessions:
    return RevokeAllSessions(
        user_repo=user_repo,
        session_repo=session_repo,
        outbox=outbox,
    )


@pytest.mark.asyncio
async def test_revokes_all_active_sessions(
    use_case: RevokeAllSessions,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)

    s1 = make_session(user_id=target.id, refresh_hash="h1")
    s2 = make_session(user_id=target.id, refresh_hash="h2")
    await session_repo.add(s1)
    await session_repo.add(s2)

    result = await use_case.execute(
        cmd=RevokeAllSessionsCommand(actor_id=actor_id, target_user_id=target.id)
    )

    assert result.revoked_count == 2
    for s in [s1, s2]:
        stored = await session_repo.get_by_id(s.id)
        assert stored is not None
        assert stored.revoked_at is not None
    assert "auth.session.revoked_all.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_no_active_sessions_returns_zero(
    use_case: RevokeAllSessions,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    target = make_user()
    await user_repo.add(target)

    result = await use_case.execute(
        cmd=RevokeAllSessionsCommand(actor_id=actor_id, target_user_id=target.id)
    )

    assert result.revoked_count == 0
    # No event emitted when nothing was revoked
    assert outbox.events == []


@pytest.mark.asyncio
async def test_unknown_user_raises(use_case: RevokeAllSessions) -> None:
    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=RevokeAllSessionsCommand(actor_id=uuid.uuid4(), target_user_id=uuid.uuid4())
        )
