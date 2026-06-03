# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ADR-0008 ticket-scope discriminator and related use cases.

Covers:
- MF-1: TicketScope enum values; FakeRedisPendingTotpLoginStore scope enforcement.
- MF-1/INV-3: Cross-scope rejection (LOGIN ticket → ENROLL route, vice versa).
- MF-2: IssueSession collaborator emits LoggedIn + sets last_login_at.
- MF-6: Per-ticket confirm-attempt cap in ConfirmTotpEnrollment.
- MF-4: AAL2 re-check — already-enrolled user rejected without mutation.
- INV-1: verify_totp resolve uses LOGIN scope.
- INV-6: ConfirmTotpEnrollment terminal branch emits LoggedIn.
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import (
    ConfirmTotpEnrollmentCommand,
    VerifyTotpCommand,
)
from auth_service.application.use_cases.confirm_totp_enrollment import (
    MAX_CONFIRM_ATTEMPTS,
    ConfirmTotpEnrollment,
)
from auth_service.application.use_cases.issue_session import IssueSession
from auth_service.application.use_cases.verify_totp import VerifyTotp
from auth_service.domain.errors import InvalidCredentialsError, TotpInvalidError
from auth_service.domain.value_objects import TicketScope

from .conftest import (
    FakeBackupCodeRepository,
    FakeEncryptionService,
    FakeEventOutbox,
    FakeJwtIssuer,
    FakeLoginAttemptRepository,
    FakePasswordHasher,
    FakeRedisPendingTotpLoginStore,
    FakeRedisTotpEnrollmentStore,
    FakeSessionRepository,
    FakeTotpService,
    FakeUserRepository,
    make_user,
)

# ---------------------------------------------------------------------------
# MF-1: TicketScope enum
# ---------------------------------------------------------------------------


def test_ticket_scope_values() -> None:
    """TicketScope has exactly 'enroll' and 'login' string values."""
    assert TicketScope.ENROLL.value == "enroll"
    assert TicketScope.LOGIN.value == "login"
    # Exactly two members
    assert set(TicketScope) == {TicketScope.ENROLL, TicketScope.LOGIN}


# ---------------------------------------------------------------------------
# MF-1: FakeRedisPendingTotpLoginStore scope enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_scope_mismatch_returns_none() -> None:
    """An ENROLL ticket presented with expected_scope=LOGIN → None (MF-1)."""
    store = FakeRedisPendingTotpLoginStore()
    user_id = uuid.uuid4()
    ticket = "ticket123"
    await store.set_ticket(ticket, user_id, TicketScope.ENROLL)

    result = await store.get_user_id(ticket, expected_scope=TicketScope.LOGIN)
    assert result is None


@pytest.mark.asyncio
async def test_store_scope_match_returns_user_id() -> None:
    """An ENROLL ticket with expected_scope=ENROLL → user_id."""
    store = FakeRedisPendingTotpLoginStore()
    user_id = uuid.uuid4()
    ticket = "ticket456"
    await store.set_ticket(ticket, user_id, TicketScope.ENROLL)

    result = await store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
    assert result == user_id


@pytest.mark.asyncio
async def test_store_missing_ticket_returns_none() -> None:
    store = FakeRedisPendingTotpLoginStore()
    result = await store.get_user_id("nonexistent", expected_scope=TicketScope.LOGIN)
    assert result is None


@pytest.mark.asyncio
async def test_store_login_ticket_rejected_by_enroll_scope() -> None:
    """A LOGIN ticket presented with expected_scope=ENROLL → None (MF-1 / INV-3)."""
    store = FakeRedisPendingTotpLoginStore()
    user_id = uuid.uuid4()
    ticket = "login_ticket"
    await store.set_ticket(ticket, user_id, TicketScope.LOGIN)

    result = await store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
    assert result is None


# ---------------------------------------------------------------------------
# MF-2: IssueSession collaborator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_session_emits_logged_in_and_sets_last_login_at() -> None:
    """IssueSession emits LoggedIn and writes last_login_at (MF-2 / D5.4.8)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    await user_repo.add(user)
    assert user.last_login_at is None

    session_repo = FakeSessionRepository()
    attempts_repo = FakeLoginAttemptRepository()
    outbox = FakeEventOutbox()

    issue = IssueSession(
        user_repo=user_repo,
        session_repo=session_repo,
        attempts_repo=attempts_repo,
        jwt_issuer=FakeJwtIssuer(),
        outbox=outbox,
    )
    result = await issue.execute(
        user=user,
        ip_address="127.0.0.1",
        user_agent="pytest",
        method="totp",
    )

    assert result.access_token.startswith("JWT:")
    assert len(result.refresh_token) > 0
    assert "auth.user.logged_in.v1" in outbox.event_types()

    updated = await user_repo.get_by_id(user.id)
    assert updated is not None
    assert updated.last_login_at is not None


# ---------------------------------------------------------------------------
# INV-3: Cross-scope ticket rejection in verify_totp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_totp_rejects_enroll_scope_ticket() -> None:
    """scope=enroll ticket sent to verify_totp lane must raise InvalidCredentialsError (MF-1)."""
    store = FakeRedisPendingTotpLoginStore()
    user_id = uuid.uuid4()
    ticket = "enroll_ticket"
    # Mint an enrollment ticket (scope=enroll)
    await store.set_ticket(ticket, user_id, TicketScope.ENROLL)

    user_repo = FakeUserRepository()
    user = make_user(has_totp=True)
    user.id = user_id  # type: ignore[misc]
    await user_repo.add(user)

    use_case = VerifyTotp(
        user_repo=user_repo,
        session_repo=FakeSessionRepository(),
        attempts_repo=FakeLoginAttemptRepository(),
        totp_service=FakeTotpService(),
        encryption_service=FakeEncryptionService(),
        backup_code_repo=FakeBackupCodeRepository(),
        totp_used_repo=type(
            "FakeTotp", (), {"exists": lambda *a, **k: False, "add": lambda *a, **k: None}
        )(),  # type: ignore[misc]
        pending_totp_store=store,
        jwt_issuer=FakeJwtIssuer(),
        hasher=FakePasswordHasher(),
        outbox=FakeEventOutbox(),
    )
    with pytest.raises(InvalidCredentialsError):
        await use_case.execute(
            cmd=VerifyTotpCommand(
                ticket_id=ticket,
                totp_code="123456",
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        )

    # Ticket must NOT be consumed (no session minted)
    still_there = await store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
    assert still_there == user_id  # ticket survived


# ---------------------------------------------------------------------------
# MF-6: Per-ticket confirm-attempt cap in ConfirmTotpEnrollment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_totp_cap_exceeded_raises_invalid_credentials() -> None:
    """6th failed confirm invalidates the ticket and raises InvalidCredentialsError (MF-6)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=False, must_change_password=True)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    await enrollment_store.set_pending(user.id, "TESTSECRET32")

    pending_totp_store = FakeRedisPendingTotpLoginStore()
    ticket = "enroll_tok"
    await pending_totp_store.set_ticket(ticket, user.id, TicketScope.ENROLL)

    uc = ConfirmTotpEnrollment(
        user_repo=user_repo,
        totp_service=FakeTotpService(always_valid=False),  # always wrong code
        encryption_service=FakeEncryptionService(),
        enrollment_store=enrollment_store,
        backup_code_repo=FakeBackupCodeRepository(),
        hasher=FakePasswordHasher(),
        outbox=FakeEventOutbox(),
        pending_totp_store=pending_totp_store,
    )

    # 5 failed attempts should raise TotpInvalidError (within cap)
    for _ in range(MAX_CONFIRM_ATTEMPTS):
        with pytest.raises(TotpInvalidError):
            await uc.execute(
                cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="000000"),
                enrollment_token=ticket,
            )
        # Ticket still alive
        assert (
            await pending_totp_store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
            is not None
        )

    # 6th attempt should invalidate the ticket → InvalidCredentialsError
    with pytest.raises(InvalidCredentialsError):
        await uc.execute(
            cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="000000"),
            enrollment_token=ticket,
        )

    # Ticket must be deleted after cap exceeded
    assert await pending_totp_store.get_user_id(ticket, expected_scope=TicketScope.ENROLL) is None


# ---------------------------------------------------------------------------
# MF-4: AAL2 re-check — already-enrolled user rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_totp_rejects_already_enrolled_user() -> None:
    """If user became TOTP-enrolled after ticket was minted, confirm must raise 401 (MF-4)."""
    user_repo = FakeUserRepository()
    # User now has TOTP (was enrolled concurrently)
    user = make_user(has_totp=True, must_change_password=True)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    await enrollment_store.set_pending(user.id, "TESTSECRET32")

    pending_totp_store = FakeRedisPendingTotpLoginStore()
    ticket = "stale_ticket"
    await pending_totp_store.set_ticket(ticket, user.id, TicketScope.ENROLL)

    original_enc = user.totp_secret_enc

    uc = ConfirmTotpEnrollment(
        user_repo=user_repo,
        totp_service=FakeTotpService(always_valid=True),
        encryption_service=FakeEncryptionService(),
        enrollment_store=enrollment_store,
        backup_code_repo=FakeBackupCodeRepository(),
        hasher=FakePasswordHasher(),
        outbox=FakeEventOutbox(),
        pending_totp_store=pending_totp_store,
    )

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(
            cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="123456"),
            enrollment_token=ticket,
        )

    # totp_secret_enc MUST NOT have been overwritten (D5.3.2 / MF-4)
    still = await user_repo.get_by_id(user.id)
    assert still is not None
    assert still.totp_secret_enc == original_enc


# ---------------------------------------------------------------------------
# INV-6: ConfirmTotpEnrollment terminal branch emits LoggedIn (enroll-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_totp_terminal_branch_emits_logged_in() -> None:
    """must_change_password=False: terminal confirm branch emits LoggedIn (MF-2/INV-6/D5.4.8)."""
    user_repo = FakeUserRepository()
    user = make_user(has_totp=False, must_change_password=False)
    await user_repo.add(user)

    enrollment_store = FakeRedisTotpEnrollmentStore()
    await enrollment_store.set_pending(user.id, "TESTSECRET32")

    pending_totp_store = FakeRedisPendingTotpLoginStore()
    ticket = "enroll_only_token"
    await pending_totp_store.set_ticket(ticket, user.id, TicketScope.ENROLL)

    session_repo = FakeSessionRepository()
    outbox = FakeEventOutbox()

    uc = ConfirmTotpEnrollment(
        user_repo=user_repo,
        totp_service=FakeTotpService(always_valid=True),
        encryption_service=FakeEncryptionService(),
        enrollment_store=enrollment_store,
        backup_code_repo=FakeBackupCodeRepository(),
        hasher=FakePasswordHasher(),
        outbox=outbox,
        pending_totp_store=pending_totp_store,
        session_repo=session_repo,
        jwt_issuer=FakeJwtIssuer(),
        attempts_repo=FakeLoginAttemptRepository(),
    )

    from auth_service.application.dto import ConfirmTotpEnrollmentTerminalDTO

    result = await uc.execute(
        cmd=ConfirmTotpEnrollmentCommand(user_id=user.id, code="123456"),
        enrollment_token=ticket,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    # Must return ConfirmTotpEnrollmentTerminalDTO (terminal branch — B-1 fix)
    assert isinstance(result, ConfirmTotpEnrollmentTerminalDTO)
    assert result.access_token.startswith("JWT:")
    assert len(result.refresh_token) > 0

    # All three events emitted in same transaction (D5.4.8)
    event_types = outbox.event_types()
    assert "auth.user.totp_enrolled.v1" in event_types
    assert "auth.user.backup_codes_regenerated.v1" in event_types
    assert "auth.user.logged_in.v1" in event_types, (
        "LoggedIn MUST be emitted on the enroll-only terminal branch (INV-6 / D5.4.8)"
    )

    # actor_id for LoggedIn MUST be the resolved user_id (D6 / INV-6)
    logged_in_events = [e for e in outbox.events if e.type == "auth.user.logged_in.v1"]
    assert len(logged_in_events) == 1
    assert str(logged_in_events[0].actor_id) == str(user.id)

    # Ticket must be deleted (terminal consume — D5.4.9)
    assert await pending_totp_store.get_user_id(ticket, expected_scope=TicketScope.ENROLL) is None

    # last_login_at must be set
    updated = await user_repo.get_by_id(user.id)
    assert updated is not None
    assert updated.last_login_at is not None
