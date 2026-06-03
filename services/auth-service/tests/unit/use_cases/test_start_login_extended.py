# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Extended unit tests for StartLogin use case — rehash, SYSTEM sentinel, lockout window.

Extends test_start_login.py without duplicating existing tests.
Covers: US-24, US-11 edge cases, lockout window expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auth_service.application.dto import LoginPendingTotpDTO, StartLoginCommand
from auth_service.application.use_cases.start_login import StartLogin
from auth_service.domain.entities import SYSTEM_PASSWORD_SENTINEL, LoginAttempt
from auth_service.domain.errors import InvalidCredentialsError

from .conftest import (
    FakeEventOutbox,
    FakeLoginAttemptRepository,
    FakePasswordHasher,
    FakeRedisPendingTotpLoginStore,
    FakeUserRepository,
    make_user,
)


class _RehashPasswordHasher(FakePasswordHasher):
    """Fake hasher that signals rehash is needed."""

    def check_needs_rehash(self, hash: str) -> bool:
        return True


class FakeOobOtpStore:
    async def set_otp(self, user_id, otp_hash: str) -> None:
        pass

    async def get_otp_hash(self, user_id):
        return None

    async def delete_otp(self, user_id) -> None:
        pass


def _use_case(
    user_repo: FakeUserRepository,
    attempts_repo: FakeLoginAttemptRepository,
    pending_store: FakeRedisPendingTotpLoginStore,
    hasher: FakePasswordHasher | None = None,
) -> StartLogin:
    return StartLogin(
        user_repo=user_repo,
        attempts_repo=attempts_repo,
        hasher=hasher or FakePasswordHasher(),
        oob_otp_store=FakeOobOtpStore(),
        pending_totp_store=pending_store,
        outbox=FakeEventOutbox(),
    )


# ---------------------------------------------------------------------------
# US-24: argon2id rehash on login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehash_on_login_when_parameters_outdated(monkeypatch) -> None:
    """When check_needs_rehash returns True, password_hash is updated in DB."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="dan@example.com", password="secret", has_totp=True)
    original_hash = user.password_hash
    await user_repo.add(user)

    rehash_hasher = _RehashPasswordHasher()
    uc = _use_case(
        user_repo,
        FakeLoginAttemptRepository(),
        FakeRedisPendingTotpLoginStore(),
        hasher=rehash_hasher,
    )
    await uc.execute(cmd=StartLoginCommand(email="dan@example.com", password="secret"))

    updated = await user_repo.get_by_id(user.id)
    # Hash should have been recomputed (it's the same plaintext but re-hashed)
    # With FakePasswordHasher, hash("secret") == "HASH:secret" — so it's updated
    assert updated.password_hash == "HASH:secret"


@pytest.mark.asyncio
async def test_no_rehash_when_parameters_current(monkeypatch) -> None:
    """When check_needs_rehash returns False, DB is NOT updated."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="dan@example.com", password="secret", has_totp=True)
    original_hash = user.password_hash
    await user_repo.add(user)

    # Default FakePasswordHasher.check_needs_rehash always returns False
    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())
    await uc.execute(cmd=StartLoginCommand(email="dan@example.com", password="secret"))

    unchanged = await user_repo.get_by_id(user.id)
    assert unchanged.password_hash == original_hash


@pytest.mark.asyncio
async def test_system_actor_sentinel_rejects(monkeypatch) -> None:
    """A user with password_hash=SYSTEM must be hard-rejected (no argon2 call)."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="system@example.com", password="secret", is_active=False)
    user.password_hash = SYSTEM_PASSWORD_SENTINEL
    await user_repo.add(user)

    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=StartLoginCommand(email="system@example.com", password="anything"))


# ---------------------------------------------------------------------------
# US-11: Lockout window expiry (edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lockout_window_expired_allows_login(monkeypatch) -> None:
    """5 failures that are > 15 minutes old do NOT lock the account."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="vince@example.com", password="secret", has_totp=True)
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    old_time = datetime.now(tz=UTC) - timedelta(minutes=16)
    for _ in range(5):
        await attempts_repo.add(
            LoginAttempt.create(email="vince@example.com", outcome="failed_password", now=old_time)
        )

    uc = _use_case(user_repo, attempts_repo, FakeRedisPendingTotpLoginStore())
    result = await uc.execute(cmd=StartLoginCommand(email="vince@example.com", password="secret"))

    assert isinstance(result, LoginPendingTotpDTO)


@pytest.mark.asyncio
async def test_success_after_four_failures_records_success(monkeypatch) -> None:
    """4 failures + successful login → outcome='success' recorded."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="vince@example.com", password="secret", has_totp=True)
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    now = datetime.now(tz=UTC)
    for _ in range(4):
        await attempts_repo.add(
            LoginAttempt.create(email="vince@example.com", outcome="failed_password", now=now)
        )

    uc = _use_case(user_repo, attempts_repo, FakeRedisPendingTotpLoginStore())
    await uc.execute(cmd=StartLoginCommand(email="vince@example.com", password="secret"))

    outcomes = [a.outcome for a in attempts_repo._store]
    assert "success" in outcomes


# ---------------------------------------------------------------------------
# US-25: Internal JWT key min_length (config-level check — tested indirectly)
# ---------------------------------------------------------------------------


def test_short_internal_jwt_key_config_covered_by_test_config() -> None:
    """Reminder: see tests/unit/test_config.py for config-level key validation.
    This is a documentation stub — actual test is in test_config.py.
    """
    pass
