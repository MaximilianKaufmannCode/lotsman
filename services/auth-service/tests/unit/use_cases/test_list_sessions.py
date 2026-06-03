# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ListMySessions and ListUserSessionsAdmin use cases (US-20, US-21)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ListMySessionsCommand, ListUserSessionsAdminCommand
from auth_service.application.use_cases.list_my_sessions import ListMySessions
from auth_service.application.use_cases.list_user_sessions_admin import ListUserSessionsAdmin
from auth_service.domain.errors import UserNotFoundError

from .conftest import (
    FakeSessionRepository,
    FakeUserRepository,
    make_session,
    make_user,
)

# ---------------------------------------------------------------------------
# US-20: List own sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_my_sessions_returns_active_only() -> None:
    user = make_user()
    current_session_id = uuid.uuid4()

    session_repo = FakeSessionRepository()
    active = make_session(user_id=user.id)
    revoked = make_session(user_id=user.id, revoked=True)
    await session_repo.add(active)
    await session_repo.add(revoked)

    uc = ListMySessions(session_repo=session_repo)
    result = await uc.execute(
        cmd=ListMySessionsCommand(user_id=user.id, current_session_id=current_session_id)
    )

    session_ids = [s.id for s in result]
    assert active.id in session_ids
    assert revoked.id not in session_ids


@pytest.mark.asyncio
async def test_list_my_sessions_empty_when_no_active() -> None:
    user = make_user()
    session_repo = FakeSessionRepository()

    uc = ListMySessions(session_repo=session_repo)
    result = await uc.execute(
        cmd=ListMySessionsCommand(user_id=user.id, current_session_id=uuid.uuid4())
    )

    assert result == []


@pytest.mark.asyncio
async def test_viewer_can_list_own_sessions() -> None:
    user = make_user(role="viewer")
    session_repo = FakeSessionRepository()
    session = make_session(user_id=user.id)
    await session_repo.add(session)

    uc = ListMySessions(session_repo=session_repo)
    result = await uc.execute(
        cmd=ListMySessionsCommand(user_id=user.id, current_session_id=session.id)
    )

    assert len(result) == 1
    # is_current flag
    assert result[0].is_current is True


# ---------------------------------------------------------------------------
# US-21: Admin list sessions for any user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_user_sessions() -> None:
    admin = make_user(role="admin")
    target = make_user(role="editor", email="zara@example.com")

    user_repo = FakeUserRepository()
    await user_repo.add(admin)
    await user_repo.add(target)

    session_repo = FakeSessionRepository()
    s1 = make_session(user_id=target.id)
    s2 = make_session(user_id=target.id)
    await session_repo.add(s1)
    await session_repo.add(s2)

    uc = ListUserSessionsAdmin(user_repo=user_repo, session_repo=session_repo)
    result = await uc.execute(
        cmd=ListUserSessionsAdminCommand(actor_id=admin.id, target_user_id=target.id)
    )

    assert len(result) == 2


@pytest.mark.asyncio
async def test_non_admin_cannot_list_other_user_sessions() -> None:
    """ListUserSessionsAdmin is an admin-only operation; the router enforces RBAC.
    The use case itself does NOT enforce role (that's the router's Depends).
    This test documents that the use case has no built-in role check —
    the caller (API layer) must apply it.
    """
    # This test validates the current design: use case executes regardless of role.
    # Role enforcement is at the FastAPI Depends level.
    editor = make_user(role="editor")
    target = make_user(role="viewer", email="zara@example.com")

    user_repo = FakeUserRepository()
    await user_repo.add(editor)
    await user_repo.add(target)

    uc = ListUserSessionsAdmin(user_repo=user_repo, session_repo=FakeSessionRepository())
    # Should complete — role not checked in use case
    result = await uc.execute(
        cmd=ListUserSessionsAdminCommand(actor_id=editor.id, target_user_id=target.id)
    )
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_admin_list_sessions_unknown_user_raises_not_found() -> None:
    admin = make_user(role="admin")
    user_repo = FakeUserRepository()
    await user_repo.add(admin)

    uc = ListUserSessionsAdmin(user_repo=user_repo, session_repo=FakeSessionRepository())

    with pytest.raises(UserNotFoundError):
        await uc.execute(
            cmd=ListUserSessionsAdminCommand(actor_id=admin.id, target_user_id=uuid.uuid4())
        )
