# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for Logout use case (US-8).

Covers:
- Happy path: session revoked, LoggedOut event emitted
- Missing refresh token: idempotent, no event
- Already-revoked session: idempotent, no event
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from auth_service.application.dto import LogoutCommand
from auth_service.application.use_cases.logout import Logout

from .conftest import (
    FakeEventOutbox,
    FakeSessionRepository,
    make_session,
    make_user,
)


def _build_uc(
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> Logout:
    return Logout(session_repo=session_repo, outbox=outbox)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_session_and_emits_event() -> None:
    user = make_user()
    plaintext = "my-refresh-token"

    session_repo = FakeSessionRepository()
    session = make_session(user_id=user.id, refresh_hash=_hash(plaintext))
    await session_repo.add(session)

    outbox = FakeEventOutbox()
    uc = _build_uc(session_repo, outbox)

    await uc.execute(cmd=LogoutCommand(refresh_token=plaintext), actor_id=user.id)

    # Session revoked
    stored = session_repo._store[session.id]
    assert stored.revoked_at is not None

    # Event emitted
    assert "auth.session.revoked.v1" in outbox.event_types()


# ---------------------------------------------------------------------------
# Missing refresh token (idempotent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_with_missing_token_is_idempotent() -> None:
    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()
    uc = _build_uc(session_repo, outbox)

    # Should not raise
    await uc.execute(cmd=LogoutCommand(refresh_token=None), actor_id=uuid.uuid4())

    assert len(outbox.events) == 0


# ---------------------------------------------------------------------------
# Already-revoked session (idempotent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_already_revoked_is_idempotent() -> None:
    user = make_user()
    plaintext = "old-revoked-token"

    session_repo = FakeSessionRepository()
    session = make_session(user_id=user.id, refresh_hash=_hash(plaintext), revoked=True)
    await session_repo.add(session)

    outbox = FakeEventOutbox()
    uc = _build_uc(session_repo, outbox)

    await uc.execute(cmd=LogoutCommand(refresh_token=plaintext), actor_id=user.id)

    # revoked_at unchanged (no second update)
    original_revoked_at = session.revoked_at
    stored = session_repo._store[session.id]
    assert stored.revoked_at == original_revoked_at
    assert len(outbox.events) == 0
