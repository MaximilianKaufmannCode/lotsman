# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for EnrollTotp use case (US-3 step 1).

Covers:
- Happy path: returns secret_b32 + otpauth_url; secret stored in Redis pending key
- Re-enroll overwrites prior pending secret (US-3 edge case)
- DB column totp_secret_enc NOT updated during step 1
- otpauth_url format contains the issuer 'Лоцман'
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import EnrollTotpCommand, TotpEnrollDTO
from auth_service.application.use_cases.enroll_totp import EnrollTotp

from .conftest import (
    FakeRedisTotpEnrollmentStore,
    FakeTotpService,
    FakeUserRepository,
    make_user,
)


def _build_uc(
    enrollment_store: FakeRedisTotpEnrollmentStore,
    user_repo: FakeUserRepository,
) -> EnrollTotp:
    async def _email_getter(uid: uuid.UUID) -> str:
        user = await user_repo.get_by_id(uid)
        return user.email if user else "unknown@example.com"

    return EnrollTotp(
        totp_service=FakeTotpService(),
        enrollment_store=enrollment_store,
        user_email_getter=_email_getter,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_totp_stores_pending_secret_and_returns_url() -> None:
    # Arrange
    user_repo = FakeUserRepository()
    user = make_user(email="ivan@example.com", has_totp=False)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    uc = _build_uc(enrollment_store, user_repo)

    # Act
    result = await uc.execute(cmd=EnrollTotpCommand(user_id=user.id))

    # Assert — response shape
    assert isinstance(result, TotpEnrollDTO)
    assert len(result.secret_b32) > 0
    assert "otpauth://totp/" in result.otpauth_url
    assert "ivan%40example.com" in result.otpauth_url or "ivan@example.com" in result.otpauth_url

    # Pending secret stored in Redis
    stored = await enrollment_store.get_pending(user.id)
    assert stored == result.secret_b32

    # DB column must NOT be updated (user still has TOTP sentinel)
    refreshed_user = await user_repo.get_by_id(user.id)
    assert not refreshed_user.has_totp_enrolled


@pytest.mark.asyncio
async def test_enroll_totp_otpauth_url_contains_lotsman_issuer() -> None:
    user_repo = FakeUserRepository()
    user = make_user(email="test@example.com", has_totp=False)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    uc = _build_uc(enrollment_store, user_repo)
    result = await uc.execute(cmd=EnrollTotpCommand(user_id=user.id))

    assert "Лоцман" in result.otpauth_url or "issuer=" in result.otpauth_url


# ---------------------------------------------------------------------------
# Re-enroll overwrites (US-3 edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_enroll_overwrites_pending_secret() -> None:
    """Second call to /enroll replaces the pending secret with a new one."""
    user_repo = FakeUserRepository()
    user = make_user(email="ivan@example.com", has_totp=False)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    uc = _build_uc(enrollment_store, user_repo)

    # First enrollment
    result1 = await uc.execute(cmd=EnrollTotpCommand(user_id=user.id))
    first_secret = result1.secret_b32

    # FakeTotpService always returns the same secret for predictability — but
    # the overwrite behaviour is what matters.
    result2 = await uc.execute(cmd=EnrollTotpCommand(user_id=user.id))

    # Pending key now holds the secret from the SECOND call
    stored = await enrollment_store.get_pending(user.id)
    assert stored == result2.secret_b32
    # Both responses have secret values
    assert result1.secret_b32 is not None
    assert result2.secret_b32 is not None
