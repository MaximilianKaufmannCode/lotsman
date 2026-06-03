# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for StartLogin use case (US-1, US-2)."""

from __future__ import annotations

import pytest

from auth_service.application.dto import (
    LoginPendingEnrollDTO,
    LoginPendingTotpDTO,
    StartLoginCommand,
)
from auth_service.application.use_cases.start_login import StartLogin
from auth_service.domain.errors import InvalidCredentialsError
from auth_service.domain.value_objects import TicketScope

from .conftest import (
    FakeEventOutbox,
    FakeLoginAttemptRepository,
    FakePasswordHasher,
    FakeRedisPendingTotpLoginStore,
    FakeUserRepository,
    make_user,
)


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
) -> StartLogin:
    return StartLogin(
        user_repo=user_repo,
        attempts_repo=attempts_repo,
        hasher=FakePasswordHasher(),
        oob_otp_store=FakeOobOtpStore(),
        pending_totp_store=pending_store,
        outbox=FakeEventOutbox(),
    )


def _cmd(email: str = "alice@example.com", password: str = "secret") -> StartLoginCommand:
    return StartLoginCommand(email=email, password=password)


@pytest.mark.asyncio
async def test_totp_enrolled_returns_pending_totp(monkeypatch) -> None:
    # Skip constant-time delay in tests
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="secret", has_totp=True)
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    pending_store = FakeRedisPendingTotpLoginStore()

    uc = _use_case(user_repo, attempts_repo, pending_store)
    result = await uc.execute(cmd=_cmd())

    assert isinstance(result, LoginPendingTotpDTO)
    assert len(result.session_ticket) > 0
    # Ticket stored in pending store with LOGIN scope (MF-1)
    stored_uid = await pending_store.get_user_id(
        result.session_ticket, expected_scope=TicketScope.LOGIN
    )
    assert stored_uid == user.id


@pytest.mark.asyncio
async def test_totp_not_enrolled_returns_pending_enroll(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="bob@example.com", password="secret", has_totp=False)
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    pending_store = FakeRedisPendingTotpLoginStore()

    uc = _use_case(user_repo, attempts_repo, pending_store)
    result = await uc.execute(cmd=StartLoginCommand(email="bob@example.com", password="secret"))

    assert isinstance(result, LoginPendingEnrollDTO)
    assert len(result.enrollment_token) > 0


@pytest.mark.asyncio
async def test_wrong_password_raises_invalid_credentials(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="correct")
    await user_repo.add(user)

    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=StartLoginCommand(email="alice@example.com", password="wrong"))


@pytest.mark.asyncio
async def test_unknown_email_raises_invalid_credentials(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()

    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd(email="nobody@example.com"))


@pytest.mark.asyncio
async def test_inactive_user_raises_invalid_credentials(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="secret", is_active=False)
    await user_repo.add(user)

    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd())


@pytest.mark.asyncio
async def test_locked_account_raises_invalid_credentials(monkeypatch) -> None:
    """5 recent failures within 15 min causes lockout."""
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    from datetime import UTC, datetime

    from auth_service.domain.entities import LoginAttempt

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="secret")
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    # Inject 5 failures within the lockout window
    now = datetime.now(tz=UTC)
    for _ in range(5):
        await attempts_repo.add(
            LoginAttempt.create(email="alice@example.com", outcome="failed_password", now=now)
        )

    uc = _use_case(user_repo, attempts_repo, FakeRedisPendingTotpLoginStore())
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd())


@pytest.mark.asyncio
async def test_records_success_attempt(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="secret", has_totp=True)
    await user_repo.add(user)

    attempts_repo = FakeLoginAttemptRepository()
    uc = _use_case(user_repo, attempts_repo, FakeRedisPendingTotpLoginStore())
    await uc.execute(cmd=_cmd())

    assert len(attempts_repo._store) == 1
    assert attempts_repo._store[0].outcome == "success"


@pytest.mark.asyncio
async def test_email_is_case_insensitive(monkeypatch) -> None:
    import auth_service.application.use_cases.start_login as mod

    async def _no_delay() -> None:
        pass

    monkeypatch.setattr(mod, "_constant_time_delay", _no_delay)

    user_repo = FakeUserRepository()
    user = make_user(email="alice@example.com", password="secret", has_totp=True)
    await user_repo.add(user)

    uc = _use_case(user_repo, FakeLoginAttemptRepository(), FakeRedisPendingTotpLoginStore())
    result = await uc.execute(cmd=StartLoginCommand(email="ALICE@EXAMPLE.COM", password="secret"))
    assert isinstance(result, LoginPendingTotpDTO)
