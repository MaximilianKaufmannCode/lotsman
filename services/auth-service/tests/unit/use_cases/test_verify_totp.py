# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for VerifyTotp use case (US-2, US-5, US-23, US-26, US-27).

Covers:
- Happy path full TOTP login → tokens issued + LoggedIn event
- Wrong TOTP code → InvalidCredentialsError
- TOTP replay same period_index → rejected
- Previous period (P-1) accepted first time
- Backup code path → marks used, emits event with method="backup_code"
- Used backup code rejected
- No unused backup codes → rejected
- Backup-code low-stock warning (≤ 2 remaining)
- Outbox payload contains no plaintext secrets
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest

from auth_service.application.dto import LoginSuccessDTO, VerifyTotpCommand
from auth_service.application.use_cases.verify_totp import VerifyTotp
from auth_service.domain.entities import BackupCode, TotpUsedCode
from auth_service.domain.errors import (
    BackupCodeInvalidError,
    InvalidCredentialsError,
    TotpInvalidError,
)
from auth_service.domain.value_objects import TicketScope

from .conftest import (
    FakeBackupCodeRepository,
    FakeEncryptionService,
    FakeEventOutbox,
    FakeJwtIssuer,
    FakeLoginAttemptRepository,
    FakePasswordHasher,
    FakeRedisPendingTotpLoginStore,
    FakeSessionRepository,
    FakeTotpService,
    FakeTotpUsedCodeRepository,
    FakeUserRepository,
    make_user,
)


def _build_uc(
    *,
    user_repo: FakeUserRepository,
    session_repo: FakeSessionRepository | None = None,
    attempts_repo: FakeLoginAttemptRepository | None = None,
    totp_service: FakeTotpService | None = None,
    backup_code_repo: FakeBackupCodeRepository | None = None,
    totp_used_repo: FakeTotpUsedCodeRepository | None = None,
    pending_store: FakeRedisPendingTotpLoginStore | None = None,
    outbox: FakeEventOutbox | None = None,
) -> VerifyTotp:
    return VerifyTotp(
        user_repo=user_repo,
        session_repo=session_repo or FakeSessionRepository(),
        attempts_repo=attempts_repo or FakeLoginAttemptRepository(),
        totp_service=totp_service or FakeTotpService(always_valid=True),
        encryption_service=FakeEncryptionService(),
        backup_code_repo=backup_code_repo or FakeBackupCodeRepository(),
        totp_used_repo=totp_used_repo or FakeTotpUsedCodeRepository(),
        pending_totp_store=pending_store or FakeRedisPendingTotpLoginStore(),
        jwt_issuer=FakeJwtIssuer(),
        hasher=FakePasswordHasher(),
        outbox=outbox or FakeEventOutbox(),
    )


async def _setup_ticket(
    pending_store: FakeRedisPendingTotpLoginStore,
    user_id: uuid.UUID,
) -> str:
    ticket_id = "ticket-abc-123"
    await pending_store.set_ticket(ticket_id, user_id, TicketScope.LOGIN)
    return ticket_id


def _cmd(ticket_id: str, totp_code: str = "123456") -> VerifyTotpCommand:
    return VerifyTotpCommand(ticket_id=ticket_id, totp_code=totp_code)


# ---------------------------------------------------------------------------
# Happy path — TOTP login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_totp_issues_tokens_and_emits_logged_in_event() -> None:
    # Arrange
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()
    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        session_repo=session_repo,
        outbox=outbox,
        pending_store=pending_store,
    )

    # Act
    result = await uc.execute(cmd=_cmd(ticket_id))

    # Assert
    assert isinstance(result, LoginSuccessDTO)
    assert result.access_token.startswith("JWT:")
    assert len(result.refresh_token) > 0
    assert result.backup_codes_warning is None

    # Session must be stored
    assert len(session_repo._store) == 1
    session = next(iter(session_repo._store.values()))
    assert session.user_id == user.id
    assert session.refresh_hash == hashlib.sha256(result.refresh_token.encode()).hexdigest()

    # LoggedIn event emitted
    assert "auth.user.logged_in.v1" in outbox.event_types()
    logged_in = next(e for e in outbox.events if e.type == "auth.user.logged_in.v1")
    assert logged_in.payload["method"] == "totp"


@pytest.mark.asyncio
async def test_verify_totp_cleans_up_pending_ticket() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(user_repo=user_repo, pending_store=pending_store)
    await uc.execute(cmd=_cmd(ticket_id))

    # Ticket must be deleted after use (scope doesn't matter — ticket is gone)
    assert await pending_store.get_user_id(ticket_id, expected_scope=TicketScope.LOGIN) is None


# ---------------------------------------------------------------------------
# Wrong TOTP code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_totp_code_raises_invalid_credentials() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    # Service rejects all codes
    totp_service = FakeTotpService(always_valid=False)
    attempts_repo = FakeLoginAttemptRepository()

    uc = _build_uc(
        user_repo=user_repo,
        totp_service=totp_service,
        attempts_repo=attempts_repo,
        pending_store=pending_store,
    )

    with pytest.raises(TotpInvalidError):
        await uc.execute(cmd=_cmd(ticket_id))

    # failed_totp recorded
    assert any(a.outcome == "failed_totp" for a in attempts_repo._store)


# ---------------------------------------------------------------------------
# TOTP anti-replay (US-23)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_replay_same_period_index_rejected() -> None:
    """Same period_index cannot be used twice (US-23)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    totp_used_repo = FakeTotpUsedCodeRepository()
    period = 100_000

    # Pre-insert the period_index (simulate first use)
    await totp_used_repo.add(TotpUsedCode.create(user_id=user.id, period_index=period))

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        totp_used_repo=totp_used_repo,
        totp_service=FakeTotpService(always_valid=True, period_index=period),
        pending_store=pending_store,
    )

    with pytest.raises(TotpInvalidError):
        await uc.execute(cmd=_cmd(ticket_id))


@pytest.mark.asyncio
async def test_totp_code_first_use_records_period_index() -> None:
    """First use records period_index in totp_used_codes (US-23)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    totp_used_repo = FakeTotpUsedCodeRepository()
    period = 99_999

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        totp_used_repo=totp_used_repo,
        totp_service=FakeTotpService(always_valid=True, period_index=period),
        pending_store=pending_store,
    )
    await uc.execute(cmd=_cmd(ticket_id))

    assert await totp_used_repo.exists(user.id, period)


@pytest.mark.asyncio
async def test_totp_previous_period_accepted_first_time() -> None:
    """P-1 period (valid_window=1) is accepted if not yet used (US-23)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    totp_used_repo = FakeTotpUsedCodeRepository()
    period_minus_one = 99_998

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        totp_used_repo=totp_used_repo,
        totp_service=FakeTotpService(always_valid=True, period_index=period_minus_one),
        pending_store=pending_store,
    )
    result = await uc.execute(cmd=_cmd(ticket_id))

    assert isinstance(result, LoginSuccessDTO)
    assert await totp_used_repo.exists(user.id, period_minus_one)


# ---------------------------------------------------------------------------
# Backup-code path (US-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_code_login_marks_used_and_emits_event() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    hasher = FakePasswordHasher()
    backup_code_repo = FakeBackupCodeRepository()
    code_str = "ABCD-EF01"
    code_hash = hasher.hash(code_str)
    code = BackupCode.create(user_id=user.id, code_hash=code_hash)
    await backup_code_repo.add_batch([code])

    outbox = FakeEventOutbox()
    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        backup_code_repo=backup_code_repo,
        outbox=outbox,
        pending_store=pending_store,
    )

    result = await uc.execute(cmd=_cmd(ticket_id), backup_code=code_str)

    assert isinstance(result, LoginSuccessDTO)
    # Code must be marked used
    assert code.used_at is not None
    # Event method="backup_code"
    logged_in = next(e for e in outbox.events if e.type == "auth.user.logged_in.v1")
    assert logged_in.payload["method"] == "backup_code"


@pytest.mark.asyncio
async def test_used_backup_code_raises_invalid_credentials() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    hasher = FakePasswordHasher()
    backup_code_repo = FakeBackupCodeRepository()
    code_str = "ABCD-EF01"
    code_hash = hasher.hash(code_str)
    code = BackupCode.create(user_id=user.id, code_hash=code_hash)
    code.used_at = datetime.now(tz=UTC)  # already used
    await backup_code_repo.add_batch([code])

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        backup_code_repo=backup_code_repo,
        pending_store=pending_store,
    )

    with pytest.raises(BackupCodeInvalidError):
        await uc.execute(cmd=_cmd(ticket_id), backup_code=code_str)


@pytest.mark.asyncio
async def test_no_unused_backup_codes_raises_invalid_credentials() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    backup_code_repo = FakeBackupCodeRepository()
    # No codes added

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        backup_code_repo=backup_code_repo,
        pending_store=pending_store,
    )

    with pytest.raises(BackupCodeInvalidError):
        await uc.execute(cmd=_cmd(ticket_id), backup_code="ABCD-EF01")


# ---------------------------------------------------------------------------
# Backup-code low-stock warning (US-26)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_code_low_stock_warning_in_response() -> None:
    """When ≤ 2 backup codes remain after login, warning is included."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    hasher = FakePasswordHasher()
    backup_code_repo = FakeBackupCodeRepository()

    # 2 total codes, 1 used → 1 unused (below threshold of 2)
    used_code_str = "AAAA-0001"
    unused_code_str = "BBBB-0002"

    used = BackupCode.create(user_id=user.id, code_hash=hasher.hash(used_code_str))
    used.used_at = datetime.now(tz=UTC)
    unused = BackupCode.create(user_id=user.id, code_hash=hasher.hash(unused_code_str))
    # The code that will be "consumed" this login
    fresh = BackupCode.create(user_id=user.id, code_hash=hasher.hash("CCCC-0003"))

    await backup_code_repo.add_batch([used, unused, fresh])

    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(
        user_repo=user_repo,
        backup_code_repo=backup_code_repo,
        pending_store=pending_store,
    )

    result = await uc.execute(cmd=_cmd(ticket_id), backup_code="CCCC-0003")

    # After using fresh, 1 unused code remains → warning
    assert result.backup_codes_warning is not None
    assert result.backup_codes_warning <= 2


# ---------------------------------------------------------------------------
# Outbox payload has no plaintext secrets (US-27)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logged_in_event_payload_has_no_plaintext_secrets() -> None:
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)

    outbox = FakeEventOutbox()
    pending_store = FakeRedisPendingTotpLoginStore()
    ticket_id = await _setup_ticket(pending_store, user.id)

    uc = _build_uc(user_repo=user_repo, outbox=outbox, pending_store=pending_store)
    await uc.execute(cmd=_cmd(ticket_id))

    event = next(e for e in outbox.events if e.type == "auth.user.logged_in.v1")
    payload_str = str(event.payload)

    for forbidden in ("password", "refresh_token", "totp_secret", "oob_otp"):
        assert forbidden not in payload_str.lower(), f"Payload must not contain '{forbidden}'"


# ---------------------------------------------------------------------------
# Unknown ticket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_ticket_raises_invalid_credentials() -> None:
    uc = _build_uc(user_repo=FakeUserRepository())
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(cmd=_cmd("no-such-ticket"))
