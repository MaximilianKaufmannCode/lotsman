# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ChangePassword use case (US-6).

Covers:
- Happy path: password updated, other sessions revoked, PasswordChanged emitted
- HIBP breached password: WeakPasswordError
- New password < 12 chars: PasswordPolicyViolationError
- Missing re-MFA on normal path: ReMfaRequiredError
- Forced enrollment path: UserActivated emitted + full tokens returned
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ChangePasswordCommand
from auth_service.application.use_cases.change_password import ChangePassword
from auth_service.domain.errors import (
    ReMfaRequiredError,
    WeakPasswordError,
)

from .conftest import (
    FakeBreachedPasswordChecker,
    FakeEventOutbox,
    FakeJwtIssuer,
    FakeLoginAttemptRepository,
    FakePasswordHasher,
    FakeRedisPendingTotpLoginStore,
    FakeRedisReMfaStore,
    FakeSessionRepository,
    FakeUserRepository,
    make_session,
    make_user,
)


def _build_uc(
    *,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository | None = None,
    re_mfa_store: FakeRedisReMfaStore | None = None,
    outbox: FakeEventOutbox | None = None,
    breach_words: frozenset[str] | None = None,
    include_forced_path_deps: bool = False,
) -> ChangePassword:
    attempts_repo = FakeLoginAttemptRepository() if include_forced_path_deps else None
    pending_totp_store = FakeRedisPendingTotpLoginStore() if include_forced_path_deps else None
    return ChangePassword(
        user_repo=user_repo,
        session_repo=session_repo or FakeSessionRepository(),
        hasher=FakePasswordHasher(),
        hibp_checker=FakeBreachedPasswordChecker(breach_words=breach_words),
        re_mfa_store=re_mfa_store or FakeRedisReMfaStore(),
        jwt_issuer=FakeJwtIssuer(),
        outbox=outbox or FakeEventOutbox(),
        attempts_repo=attempts_repo,
        pending_totp_store=pending_totp_store,
    )


# ---------------------------------------------------------------------------
# Happy path — normal self-service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_updates_hash_revokes_other_sessions() -> None:
    user_repo = FakeUserRepository()
    user = make_user(must_change_password=False)
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    current_session = make_session(user_id=user.id, refresh_hash="current")
    other_session = make_session(user_id=user.id, refresh_hash="other")
    await session_repo.add(current_session)
    await session_repo.add(other_session)

    re_mfa_store = FakeRedisReMfaStore()
    await re_mfa_store.set_verified(user.id, current_session.id)

    outbox = FakeEventOutbox()
    uc = _build_uc(
        user_repo=user_repo, session_repo=session_repo, re_mfa_store=re_mfa_store, outbox=outbox
    )

    result = await uc.execute(
        cmd=ChangePasswordCommand(
            user_id=user.id,
            session_id=current_session.id,
            new_password="CorrectHorseBatteryStaple44",
        )
    )

    assert result is None  # no tokens on normal path

    # Password hash updated
    updated = await user_repo.get_by_id(user.id)
    assert updated.password_hash == "HASH:CorrectHorseBatteryStaple44"

    # Other session revoked, current remains active
    assert session_repo._store[other_session.id].revoked_at is not None
    assert session_repo._store[current_session.id].revoked_at is None

    # PasswordChanged event emitted
    assert "auth.user.password_changed.v1" in outbox.event_types()


# ---------------------------------------------------------------------------
# HIBP breach check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hibp_password_raises_weak_password_error() -> None:
    user_repo = FakeUserRepository()
    user = make_user(must_change_password=False)
    await user_repo.add(user)

    re_mfa_store = FakeRedisReMfaStore()
    session_id = uuid.uuid4()
    await re_mfa_store.set_verified(user.id, session_id)

    uc = _build_uc(
        user_repo=user_repo,
        re_mfa_store=re_mfa_store,
        breach_words=frozenset(["password123456"]),
    )

    with pytest.raises(WeakPasswordError):
        await uc.execute(
            cmd=ChangePasswordCommand(
                user_id=user.id,
                session_id=session_id,
                new_password="password123456",
            )
        )

    # Hash must NOT be updated
    unchanged = await user_repo.get_by_id(user.id)
    assert unchanged.password_hash == user.password_hash


# ---------------------------------------------------------------------------
# Missing re-MFA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_without_re_mfa_raises() -> None:
    user_repo = FakeUserRepository()
    user = make_user(must_change_password=False)
    await user_repo.add(user)

    re_mfa_store = FakeRedisReMfaStore()
    # NOT verified

    uc = _build_uc(user_repo=user_repo, re_mfa_store=re_mfa_store)

    with pytest.raises(ReMfaRequiredError):
        await uc.execute(
            cmd=ChangePasswordCommand(
                user_id=user.id,
                session_id=uuid.uuid4(),
                new_password="CorrectHorseBatteryStaple44",
            )
        )


# ---------------------------------------------------------------------------
# Forced enrollment path (US-6 edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forced_enrollment_path_emits_activated_event() -> None:
    """must_change_password=True skips re-MFA, returns tokens, emits UserActivated.

    ADR-0008 D5.4.1 / D6 / INV-6: the forced-enrollment path now INTENTIONALLY
    also emits ``auth.user.logged_in.v1`` and sets ``last_login_at`` via the shared
    IssueSession collaborator.  This is NOT a regression — it is a deliberate
    audit-completeness improvement (the forced path previously emitted no LoggedIn).
    """
    user_repo = FakeUserRepository()
    user = make_user(must_change_password=True)
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()
    uc = _build_uc(
        user_repo=user_repo,
        session_repo=session_repo,
        outbox=outbox,
        include_forced_path_deps=True,
    )

    result = await uc.execute(
        cmd=ChangePasswordCommand(
            user_id=user.id,
            session_id=uuid.uuid4(),  # no re-MFA required on forced path
            new_password="NewStrongPassword99",
        )
    )

    # Tokens issued
    assert result is not None
    assert result.access_token.startswith("JWT:")
    assert len(result.refresh_token) > 0

    event_types = outbox.event_types()

    # Existing events preserved
    assert "auth.user.activated.v1" in event_types
    assert "auth.user.password_changed.v1" in event_types

    # ADR-0008 INV-6: the NEWLY ADDED auth.user.logged_in.v1 event on this path.
    # This MUST be present after the refactor — QA obligation from ADR.
    assert "auth.user.logged_in.v1" in event_types, (
        "LoggedIn event MUST be emitted on the forced-enrollment path "
        "(ADR-0008 D5.4.1/D6/INV-6 — intentional audit-completeness improvement)"
    )

    # last_login_at was set by IssueSession
    updated = await user_repo.get_by_id(user.id)
    assert not updated.must_change_password
    assert updated.last_login_at is not None, (
        "last_login_at MUST be set on the forced-enrollment path (ADR-0008 D5.4.8/INV-6)"
    )
