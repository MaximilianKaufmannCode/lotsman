# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RevokeSession use case."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import RevokeSessionCommand
from auth_service.application.use_cases.revoke_session import RevokeSession
from auth_service.domain.errors import SessionNotFoundError

from .conftest import (
    FakeEventOutbox,
    FakeSessionRepository,
    make_session,
)


@pytest.fixture()
def session_repo() -> FakeSessionRepository:
    return FakeSessionRepository()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(session_repo: FakeSessionRepository, outbox: FakeEventOutbox) -> RevokeSession:
    return RevokeSession(session_repo=session_repo, outbox=outbox)


@pytest.mark.asyncio
async def test_revokes_own_session(
    use_case: RevokeSession,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> None:
    user_id = uuid.uuid4()
    s = make_session(user_id=user_id)
    await session_repo.add(s)

    await use_case.execute(
        cmd=RevokeSessionCommand(
            actor_id=user_id,
            actor_role="editor",
            target_user_id=user_id,
            session_id=s.id,
        )
    )

    stored = await session_repo.get_by_id(s.id)
    assert stored is not None
    assert stored.revoked_at is not None
    assert "auth.session.revoked.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_admin_revokes_other_user_session(
    use_case: RevokeSession,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> None:
    admin_id = uuid.uuid4()
    target_id = uuid.uuid4()
    s = make_session(user_id=target_id)
    await session_repo.add(s)

    await use_case.execute(
        cmd=RevokeSessionCommand(
            actor_id=admin_id,
            actor_role="admin",
            target_user_id=target_id,
            session_id=s.id,
        )
    )

    stored = await session_repo.get_by_id(s.id)
    assert stored is not None
    assert stored.revoked_at is not None


@pytest.mark.asyncio
async def test_session_user_id_mismatch_raises(
    use_case: RevokeSession,
    session_repo: FakeSessionRepository,
) -> None:
    """target_user_id must match the session's user_id; mismatch raises SessionNotFoundError."""
    user_id = uuid.uuid4()
    wrong_target_id = uuid.uuid4()
    s = make_session(user_id=user_id)
    await session_repo.add(s)

    with pytest.raises(SessionNotFoundError):
        await use_case.execute(
            cmd=RevokeSessionCommand(
                actor_id=uuid.uuid4(),
                actor_role="editor",
                target_user_id=wrong_target_id,  # doesn't match session.user_id
                session_id=s.id,
            )
        )


@pytest.mark.asyncio
async def test_unknown_session_raises(use_case: RevokeSession) -> None:
    user_id = uuid.uuid4()
    with pytest.raises(SessionNotFoundError):
        await use_case.execute(
            cmd=RevokeSessionCommand(
                actor_id=user_id,
                actor_role="editor",
                target_user_id=user_id,
                session_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_idempotent_on_already_revoked(
    use_case: RevokeSession,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> None:
    user_id = uuid.uuid4()
    s = make_session(user_id=user_id, revoked=True)
    await session_repo.add(s)

    # Already revoked; use case should treat this as idempotent (no error, no event)
    await use_case.execute(
        cmd=RevokeSessionCommand(
            actor_id=user_id,
            actor_role="editor",
            target_user_id=user_id,
            session_id=s.id,
        )
    )

    assert outbox.events == []
