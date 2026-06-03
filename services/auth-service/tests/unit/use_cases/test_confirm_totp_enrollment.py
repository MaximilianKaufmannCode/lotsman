# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ConfirmTotpEnrollment use case (US-3 steps 2).

Covers:
- Happy path: secret persisted (encrypted), Redis pending key deleted, 10 backup codes returned,
  TotpEnrolled + BackupCodesGenerated events emitted
- Wrong code: pending key remains, DB not updated
- Expired pending key (Redis key missing): TotpEnrollmentExpiredError
- 10 backup codes generated in XXXX-XXXX format
"""

from __future__ import annotations

import re

import pytest

from auth_service.application.dto import ConfirmTotpEnrollmentCommand, TotpConfirmDTO
from auth_service.application.use_cases.confirm_totp_enrollment import ConfirmTotpEnrollment
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import TotpEnrollmentExpiredError, TotpInvalidError

from .conftest import (
    FakeBackupCodeRepository,
    FakeEncryptionService,
    FakeEventOutbox,
    FakePasswordHasher,
    FakeRedisTotpEnrollmentStore,
    FakeTotpService,
    FakeUserRepository,
    make_user,
)

_BACKUP_CODE_PATTERN = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")


def _build_uc(
    *,
    user_repo: FakeUserRepository,
    enrollment_store: FakeRedisTotpEnrollmentStore,
    totp_service: FakeTotpService | None = None,
    outbox: FakeEventOutbox | None = None,
    backup_code_repo: FakeBackupCodeRepository | None = None,
) -> ConfirmTotpEnrollment:
    return ConfirmTotpEnrollment(
        user_repo=user_repo,
        totp_service=totp_service or FakeTotpService(always_valid=True),
        encryption_service=FakeEncryptionService(),
        enrollment_store=enrollment_store,
        backup_code_repo=backup_code_repo or FakeBackupCodeRepository(),
        hasher=FakePasswordHasher(),
        outbox=outbox or FakeEventOutbox(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_enrollment_persists_secret_and_returns_backup_codes() -> None:
    # Arrange — must_change_password=True so we exercise the non-terminal branch:
    # confirm returns TotpConfirmDTO (backup codes only, no tokens); ticket stays alive.
    user_repo = FakeUserRepository()
    user = make_user(has_totp=False, must_change_password=True)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    await enrollment_store.set_pending(user.id, "JBSWY3DPEHPK3PXP")

    backup_code_repo = FakeBackupCodeRepository()
    outbox = FakeEventOutbox()
    uc = _build_uc(
        user_repo=user_repo,
        enrollment_store=enrollment_store,
        backup_code_repo=backup_code_repo,
        outbox=outbox,
    )

    # Act
    result = await uc.execute(cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="123456"))

    # Assert
    assert isinstance(result, TotpConfirmDTO)
    assert len(result.backup_codes) == 10
    for code in result.backup_codes:
        assert _BACKUP_CODE_PATTERN.match(code), f"Backup code format invalid: {code}"

    # DB column updated (encrypted)
    updated_user = await user_repo.get_by_id(user.id)
    assert updated_user.totp_secret_enc != TOTP_SENTINEL
    assert updated_user.totp_secret_enc.startswith(b"ENC:")

    # Redis pending key deleted
    assert await enrollment_store.get_pending(user.id) is None

    # 10 backup codes persisted
    persisted = await backup_code_repo.list_unused_for_user(user.id)
    assert len(persisted) == 10

    # Events emitted
    assert "auth.user.totp_enrolled.v1" in outbox.event_types()
    assert "auth.user.backup_codes_regenerated.v1" in outbox.event_types()


# ---------------------------------------------------------------------------
# Wrong code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_code_leaves_pending_key_intact() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=False)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    await enrollment_store.set_pending(user.id, "JBSWY3DPEHPK3PXP")

    uc = _build_uc(
        user_repo=user_repo,
        enrollment_store=enrollment_store,
        totp_service=FakeTotpService(always_valid=False),
    )

    with pytest.raises(TotpInvalidError):
        await uc.execute(cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="000000"))

    # Pending key must still exist (not deleted on wrong code)
    assert await enrollment_store.get_pending(user.id) is not None

    # DB column unchanged (still sentinel)
    unchanged = await user_repo.get_by_id(user.id)
    assert not unchanged.has_totp_enrolled


# ---------------------------------------------------------------------------
# Expired pending key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_pending_key_raises_enrollment_expired() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=False)
    await user_repo.add(user)

    # No pending key in Redis (simulates TTL expiry)
    enrollment_store = FakeRedisTotpEnrollmentStore()
    uc = _build_uc(user_repo=user_repo, enrollment_store=enrollment_store)

    with pytest.raises(TotpEnrollmentExpiredError):
        await uc.execute(cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="123456"))
