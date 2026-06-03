# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ResetTotpAdmin use case (US-16)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ResetTotpAdminCommand
from auth_service.application.use_cases.reset_totp_admin import ResetTotpAdmin
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import ReMfaFailedError, SelfActionForbiddenError, UserNotFoundError

from .conftest import (
    FakeBackupCodeRepository,
    FakeEncryptionService,
    FakeEventOutbox,
    FakeSessionRepository,
    FakeTotpService,
    FakeUserRepository,
    make_session,
    make_user,
)


def _make_uc(
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository,
    backup_code_repo: FakeBackupCodeRepository,
    outbox: FakeEventOutbox,
    *,
    totp_always_valid: bool = True,
) -> ResetTotpAdmin:
    return ResetTotpAdmin(
        user_repo=user_repo,
        session_repo=session_repo,
        backup_code_repo=backup_code_repo,
        totp_service=FakeTotpService(always_valid=totp_always_valid),
        encryption_service=FakeEncryptionService(),
        outbox=outbox,
    )


@pytest.mark.asyncio
async def test_resets_totp_and_revokes_sessions() -> None:
    user_repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    backup_code_repo = FakeBackupCodeRepository()
    outbox = FakeEventOutbox()

    admin = make_user(role="admin", email="admin@example.com", has_totp=True)
    target = make_user(email="target@example.com", has_totp=True)
    await user_repo.add(admin)
    await user_repo.add(target)

    s = make_session(user_id=target.id)
    await session_repo.add(s)

    uc = _make_uc(user_repo, session_repo, backup_code_repo, outbox)
    await uc.execute(
        cmd=ResetTotpAdminCommand(
            actor_id=admin.id,
            target_user_id=target.id,
            admin_totp_code="123456",
        )
    )

    stored = await user_repo.get_by_id(target.id)
    assert stored is not None
    assert stored.totp_secret_enc == TOTP_SENTINEL

    stored_session = await session_repo.get_by_id(s.id)
    assert stored_session is not None
    assert stored_session.revoked_at is not None

    event_types = outbox.event_types()
    assert "auth.user.totp_reset.v1" in event_types


@pytest.mark.asyncio
async def test_self_reset_raises() -> None:
    user_repo = FakeUserRepository()
    admin = make_user(role="admin", has_totp=True)
    await user_repo.add(admin)

    uc = _make_uc(user_repo, FakeSessionRepository(), FakeBackupCodeRepository(), FakeEventOutbox())
    with pytest.raises(SelfActionForbiddenError):
        await uc.execute(
            cmd=ResetTotpAdminCommand(
                actor_id=admin.id,
                target_user_id=admin.id,  # same — self-reset
                admin_totp_code="123456",
            )
        )


@pytest.mark.asyncio
async def test_wrong_totp_code_raises_re_mfa_failed() -> None:
    user_repo = FakeUserRepository()
    admin = make_user(role="admin", email="admin@example.com", has_totp=True)
    target = make_user(email="target@example.com", has_totp=True)
    await user_repo.add(admin)
    await user_repo.add(target)

    uc = _make_uc(
        user_repo,
        FakeSessionRepository(),
        FakeBackupCodeRepository(),
        FakeEventOutbox(),
        totp_always_valid=False,  # force TOTP verification to fail
    )
    with pytest.raises(ReMfaFailedError):
        await uc.execute(
            cmd=ResetTotpAdminCommand(
                actor_id=admin.id,
                target_user_id=target.id,
                admin_totp_code="000000",
            )
        )


@pytest.mark.asyncio
async def test_unknown_target_raises() -> None:
    user_repo = FakeUserRepository()
    admin = make_user(role="admin", email="admin@example.com", has_totp=True)
    await user_repo.add(admin)

    uc = _make_uc(user_repo, FakeSessionRepository(), FakeBackupCodeRepository(), FakeEventOutbox())
    with pytest.raises(UserNotFoundError):
        await uc.execute(
            cmd=ResetTotpAdminCommand(
                actor_id=admin.id,
                target_user_id=uuid.uuid4(),
                admin_totp_code="123456",
            )
        )


@pytest.mark.asyncio
async def test_deletes_backup_codes_for_target() -> None:
    from auth_service.domain.entities import BackupCode

    user_repo = FakeUserRepository()
    backup_code_repo = FakeBackupCodeRepository()
    admin = make_user(role="admin", email="admin@example.com", has_totp=True)
    target = make_user(email="target@example.com", has_totp=True)
    await user_repo.add(admin)
    await user_repo.add(target)

    # Add some backup codes for target
    code = BackupCode.create(user_id=target.id, code_hash="HASH:test123")
    await backup_code_repo.add_batch([code])

    uc = _make_uc(user_repo, FakeSessionRepository(), backup_code_repo, FakeEventOutbox())
    await uc.execute(
        cmd=ResetTotpAdminCommand(
            actor_id=admin.id,
            target_user_id=target.id,
            admin_totp_code="123456",
        )
    )

    remaining = await backup_code_repo.list_unused_for_user(target.id)
    assert remaining == []
