# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ReMfaCheck use case (US-22).

Covers:
- Happy path: code accepted, Redis flag set, success recorded
- Wrong TOTP code: TotpInvalidError, failure recorded
- Replay of same TOTP code within same period_index: TotpCodeAlreadyUsedError
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ReMfaCheckCommand
from auth_service.application.use_cases.re_mfa_check import ReMfaCheck
from auth_service.domain.entities import TotpUsedCode
from auth_service.domain.errors import TotpCodeAlreadyUsedError, TotpInvalidError

from .conftest import (
    FakeEncryptionService,
    FakeEventOutbox,
    FakeLoginAttemptRepository,
    FakeRedisReMfaStore,
    FakeTotpService,
    FakeTotpUsedCodeRepository,
    FakeUserRepository,
    make_user,
)


def _build_uc(
    *,
    user_repo: FakeUserRepository,
    totp_service: FakeTotpService | None = None,
    totp_used_repo: FakeTotpUsedCodeRepository | None = None,
    re_mfa_store: FakeRedisReMfaStore | None = None,
    attempts_repo: FakeLoginAttemptRepository | None = None,
) -> ReMfaCheck:
    return ReMfaCheck(
        user_repo=user_repo,
        totp_service=totp_service or FakeTotpService(always_valid=True),
        encryption_service=FakeEncryptionService(),
        totp_used_repo=totp_used_repo or FakeTotpUsedCodeRepository(),
        re_mfa_store=re_mfa_store or FakeRedisReMfaStore(),
        attempts_repo=attempts_repo or FakeLoginAttemptRepository(),
        outbox=FakeEventOutbox(),
    )


def _cmd(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    totp_code: str = "123456",
) -> ReMfaCheckCommand:
    return ReMfaCheckCommand(user_id=user_id, session_id=session_id, totp_code=totp_code)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_mfa_check_sets_redis_flag_on_success() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    session_id = uuid.uuid4()
    re_mfa_store = FakeRedisReMfaStore()
    totp_used_repo = FakeTotpUsedCodeRepository()
    attempts_repo = FakeLoginAttemptRepository()

    uc = _build_uc(
        user_repo=user_repo,
        re_mfa_store=re_mfa_store,
        totp_used_repo=totp_used_repo,
        attempts_repo=attempts_repo,
    )

    result = await uc.execute(cmd=_cmd(user.id, session_id))

    assert result.mfa_verified is True
    assert await re_mfa_store.is_verified(user.id, session_id)
    assert any(a.outcome == "success" for a in attempts_repo._store)


# ---------------------------------------------------------------------------
# Wrong TOTP code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_totp_at_re_mfa_raises_invalid() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    session_id = uuid.uuid4()
    re_mfa_store = FakeRedisReMfaStore()
    attempts_repo = FakeLoginAttemptRepository()

    uc = _build_uc(
        user_repo=user_repo,
        totp_service=FakeTotpService(always_valid=False),
        re_mfa_store=re_mfa_store,
        attempts_repo=attempts_repo,
    )

    with pytest.raises(TotpInvalidError):
        await uc.execute(cmd=_cmd(user.id, session_id))

    # Redis flag NOT set
    assert not await re_mfa_store.is_verified(user.id, session_id)
    # Failure recorded
    assert any(a.outcome == "failed_totp" for a in attempts_repo._store)


# ---------------------------------------------------------------------------
# TOTP replay (US-22 edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_mfa_totp_replay_rejected() -> None:
    """Same TOTP code at same period_index must be rejected (anti-replay)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    period = 999_999
    totp_used_repo = FakeTotpUsedCodeRepository()
    # Pre-insert the period_index (simulate prior use)
    await totp_used_repo.add(TotpUsedCode.create(user_id=user.id, period_index=period))

    session_id = uuid.uuid4()
    uc = _build_uc(
        user_repo=user_repo,
        totp_service=FakeTotpService(always_valid=True, period_index=period),
        totp_used_repo=totp_used_repo,
    )

    with pytest.raises(TotpCodeAlreadyUsedError):
        await uc.execute(cmd=_cmd(user.id, session_id))
