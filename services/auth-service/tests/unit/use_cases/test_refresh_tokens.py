# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RefreshTokens use case (US-9, US-10).

Covers:
- Happy path: valid refresh → new tokens issued, old session revoked, SessionRotated emitted
- Expired refresh (> 7 days): SessionExpiredError
- Revoked token → chain revoke + SessionReuseDetected emitted (HIGH severity)
- Unknown token → SessionReuseDetected emitted with actor_id=null
- Inactive user cannot refresh
"""

from __future__ import annotations

import hashlib

import pytest

from auth_service.application.dto import RefreshTokensCommand
from auth_service.application.use_cases.refresh_tokens import RefreshTokens
from auth_service.domain.errors import InvalidCredentialsError, SessionExpiredError

from .conftest import (
    FakeEventOutbox,
    FakeJwtIssuer,
    FakeSessionRepository,
    FakeUserRepository,
    make_session,
    make_user,
)


def _build_uc(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    outbox: FakeEventOutbox,
) -> RefreshTokens:
    return RefreshTokens(
        user_repo=user_repo,
        session_repo=session_repo,
        jwt_issuer=FakeJwtIssuer(),
        outbox=outbox,
    )


def _cmd(refresh_token: str) -> RefreshTokensCommand:
    return RefreshTokensCommand(refresh_token=refresh_token)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_rotates_session_and_returns_new_tokens() -> None:
    # Arrange
    user_repo = FakeUserRepository()
    user = make_user()
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    plaintext = "original-refresh-token"
    old_session = make_session(user_id=user.id, refresh_hash=_hash(plaintext))
    await session_repo.add(old_session)

    outbox = FakeEventOutbox()
    uc = _build_uc(user_repo, session_repo, outbox)

    # Act
    result = await uc.execute(cmd=_cmd(plaintext))

    # Assert — new tokens returned
    assert result.access_token.startswith("JWT:")
    assert len(result.refresh_token) > 0
    assert result.refresh_token != plaintext

    # Old session is revoked
    old = session_repo._store[old_session.id]
    assert old.revoked_at is not None

    # New session is active
    new_sessions = [
        s for s in session_repo._store.values() if s.refresh_hash == _hash(result.refresh_token)
    ]
    assert len(new_sessions) == 1
    new_session = new_sessions[0]
    assert new_session.revoked_at is None

    # New session inherits original expires_at (absolute TTL, no sliding)
    assert new_session.expires_at == old_session.expires_at

    # SessionRotated event emitted
    assert "auth.session.rotated.v1" in outbox.event_types()


# ---------------------------------------------------------------------------
# Expired refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_refresh_token_raises_session_expired() -> None:
    user_repo = FakeUserRepository()
    user = make_user()
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    plaintext = "expired-token"
    old_session = make_session(user_id=user.id, refresh_hash=_hash(plaintext), ttl_days=-1)
    await session_repo.add(old_session)

    outbox = FakeEventOutbox()
    uc = _build_uc(user_repo, session_repo, outbox)

    with pytest.raises(SessionExpiredError):
        await uc.execute(cmd=_cmd(plaintext))

    # No new session, no reuse event
    assert "auth.session.reuse_detected.v1" not in outbox.event_types()


# ---------------------------------------------------------------------------
# Reuse detection (US-10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_refresh_triggers_chain_revoke() -> None:
    """Presenting a revoked token revokes ALL active sessions + emits reuse event."""
    user_repo = FakeUserRepository()
    user = make_user()
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    plaintext = "old-rotated-token"

    # Old session (already rotated — revoked_at set)
    revoked_session = make_session(user_id=user.id, refresh_hash=_hash(plaintext), revoked=True)
    await session_repo.add(revoked_session)

    # An active session that should be chain-revoked
    active_session = make_session(user_id=user.id, refresh_hash="other-hash")
    await session_repo.add(active_session)

    outbox = FakeEventOutbox()
    uc = _build_uc(user_repo, session_repo, outbox)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd(plaintext))

    # Both sessions now revoked
    assert all(s.revoked_at is not None for s in session_repo._store.values())

    # High-severity event emitted
    assert "auth.session.reuse_detected.v1" in outbox.event_types()
    event = next(e for e in outbox.events if e.type == "auth.session.reuse_detected.v1")
    assert event.payload.get("severity") == "HIGH"


@pytest.mark.asyncio
async def test_unknown_refresh_token_emits_reuse_detected_event() -> None:
    """Unknown token (no DB row) still emits anomaly event."""
    user_repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()
    uc = _build_uc(user_repo, session_repo, outbox)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd("completely-unknown-token"))

    assert "auth.session.reuse_detected.v1" in outbox.event_types()


# ---------------------------------------------------------------------------
# Inactive user cannot refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_user_cannot_refresh() -> None:
    user_repo = FakeUserRepository()
    user = make_user(is_active=False)
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    plaintext = "some-token"
    session = make_session(user_id=user.id, refresh_hash=_hash(plaintext))
    await session_repo.add(session)

    outbox = FakeEventOutbox()
    uc = _build_uc(user_repo, session_repo, outbox)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd(plaintext))
