# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RecordLoginAttempt and RecordSessionReuse helper use cases.

These are thin orchestration use cases (US-11, US-10) that do exactly one thing:
write a record and/or emit an event. Tests confirm the correct record is created
and the correct event is emitted across all outcome variants.
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import RecordLoginAttemptCommand
from auth_service.application.use_cases.record_login_attempt import RecordLoginAttempt
from auth_service.application.use_cases.record_session_reuse import RecordSessionReuse

from .conftest import (
    FakeEventOutbox,
    FakeLoginAttemptRepository,
    FakeSessionRepository,
    make_session,
)

# ---------------------------------------------------------------------------
# RecordLoginAttempt — US-11 helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_login_attempt_stores_success_outcome() -> None:
    """Successful login outcome is persisted to the login_attempts store."""
    # Arrange
    repo = FakeLoginAttemptRepository()
    uc = RecordLoginAttempt(attempts_repo=repo)

    # Act
    await uc.execute(
        cmd=RecordLoginAttemptCommand(
            email="user@example.com",
            outcome="success",
            ip_address="10.0.0.1",
            user_agent="pytest/1.0",
        )
    )

    # Assert
    assert len(repo._store) == 1
    attempt = repo._store[0]
    assert attempt.email == "user@example.com"
    assert attempt.outcome == "success"
    assert attempt.ip_address == "10.0.0.1"


@pytest.mark.asyncio
async def test_record_login_attempt_stores_failed_password_outcome() -> None:
    """Failed-password outcome is stored; future lockout checks will count it."""
    repo = FakeLoginAttemptRepository()
    uc = RecordLoginAttempt(attempts_repo=repo)

    await uc.execute(
        cmd=RecordLoginAttemptCommand(
            email="user@example.com",
            outcome="failed_password",
            ip_address=None,
            user_agent=None,
        )
    )

    assert repo._store[0].outcome == "failed_password"


@pytest.mark.asyncio
async def test_record_login_attempt_stores_failed_totp_outcome() -> None:
    """Failed-TOTP outcome is stored; lockout policy counts both phases."""
    repo = FakeLoginAttemptRepository()
    uc = RecordLoginAttempt(attempts_repo=repo)

    await uc.execute(
        cmd=RecordLoginAttemptCommand(
            email="user@example.com",
            outcome="failed_totp",
        )
    )

    assert repo._store[0].outcome == "failed_totp"


@pytest.mark.asyncio
async def test_record_login_attempt_stores_locked_outcome() -> None:
    """Requests while already locked produce a 'locked' outcome record."""
    repo = FakeLoginAttemptRepository()
    uc = RecordLoginAttempt(attempts_repo=repo)

    await uc.execute(
        cmd=RecordLoginAttemptCommand(
            email="user@example.com",
            outcome="locked",
        )
    )

    assert repo._store[0].outcome == "locked"


@pytest.mark.asyncio
async def test_record_login_attempt_multiple_calls_accumulate() -> None:
    """Each call appends a new record — the store is append-only."""
    repo = FakeLoginAttemptRepository()
    uc = RecordLoginAttempt(attempts_repo=repo)
    email = "user@example.com"

    for outcome in ("failed_password", "failed_password", "success"):
        await uc.execute(cmd=RecordLoginAttemptCommand(email=email, outcome=outcome))

    assert len(repo._store) == 3
    outcomes = [a.outcome for a in repo._store]
    assert outcomes == ["failed_password", "failed_password", "success"]


# ---------------------------------------------------------------------------
# RecordSessionReuse — US-10 chain-revoke helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_session_reuse_revokes_all_sessions_for_known_user() -> None:
    """Chain-revoke: all active sessions for the user are revoked."""
    # Arrange
    user_id = uuid.uuid4()
    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()

    s1 = make_session(user_id=user_id, refresh_hash="h1")
    s2 = make_session(user_id=user_id, refresh_hash="h2")
    await session_repo.add(s1)
    await session_repo.add(s2)

    uc = RecordSessionReuse(session_repo=session_repo, outbox=outbox)

    # Act
    await uc.execute(user_id=user_id)

    # Assert: all sessions revoked
    for s in [s1, s2]:
        stored = await session_repo.get_by_id(s.id)
        assert stored is not None
        assert stored.revoked_at is not None

    # Assert: reuse-detected event emitted
    assert "auth.session.reuse_detected.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_record_session_reuse_emits_event_even_for_unknown_user() -> None:
    """When user_id is None (orphan token), chain-revoke is a no-op
    but the reuse event must still be emitted (ADR-0003 §10)."""
    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()

    uc = RecordSessionReuse(session_repo=session_repo, outbox=outbox)

    await uc.execute(user_id=None)

    # No sessions to revoke
    assert session_repo._store == {}
    # Event still emitted
    assert "auth.session.reuse_detected.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_record_session_reuse_event_has_high_severity_marker() -> None:
    """The reuse-detected event envelope must carry HIGH severity (ADR-0003 §10)."""
    outbox = FakeEventOutbox()
    uc = RecordSessionReuse(
        session_repo=FakeSessionRepository(),
        outbox=outbox,
    )
    user_id = uuid.uuid4()

    await uc.execute(user_id=user_id)

    assert len(outbox.events) == 1
    envelope = outbox.events[0]
    # Severity is embedded in the event payload (ADR-0003 §10)
    assert envelope.type == "auth.session.reuse_detected.v1"
    assert envelope.payload.get("severity") == "HIGH"
    # The payload actor_id identifies the affected user
    assert envelope.payload.get("actor_id") == str(user_id)


@pytest.mark.asyncio
async def test_record_session_reuse_does_not_revoke_already_revoked_sessions() -> None:
    """Idempotency: already-revoked sessions keep their original revoked_at timestamp."""
    from datetime import UTC, datetime, timedelta

    user_id = uuid.uuid4()
    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()

    old_time = datetime.now(UTC) - timedelta(hours=1)
    s = make_session(user_id=user_id, revoked=True)
    # Simulate specific revocation timestamp recorded one hour ago
    s.revoked_at = old_time
    await session_repo.add(s)

    uc = RecordSessionReuse(session_repo=session_repo, outbox=outbox)
    await uc.execute(user_id=user_id)

    stored = await session_repo.get_by_id(s.id)
    assert stored is not None
    # The original revoked_at must not be overwritten
    assert stored.revoked_at == old_time
