# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ResolveEnrollmentTicket use case (ADR-0008 D3b / M-2).

Covers the five cases specified in the review:
  1. ticket missing          → InvalidCredentialsError
  2. scope mismatch          → InvalidCredentialsError
  3. valid ticket, user enrolled → InvalidCredentialsError (MF-4 invariant)
  4. valid ticket, user deleted  → InvalidCredentialsError
  5. happy path              → returns user_id (uuid.UUID)
"""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.use_cases.resolve_enrollment_ticket import ResolveEnrollmentTicket
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import InvalidCredentialsError
from auth_service.domain.value_objects import TicketScope

from .conftest import FakeRedisPendingTotpLoginStore, FakeUserRepository, make_user

# ---------------------------------------------------------------------------
# Case 1: ticket missing (unknown / expired)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_ticket_raises_invalid_credentials() -> None:
    """M-2/Case-1: unknown ticket → InvalidCredentialsError (uniform generic 401)."""
    store = FakeRedisPendingTotpLoginStore()
    user_repo = FakeUserRepository()

    uc = ResolveEnrollmentTicket(pending_totp_store=store, user_repo=user_repo)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(ticket_id="no_such_ticket_ever")


# ---------------------------------------------------------------------------
# Case 2: scope mismatch (LOGIN-scoped ticket presented to ENROLL resolver)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_scoped_ticket_raises_invalid_credentials() -> None:
    """M-2/Case-2: LOGIN-scoped ticket → InvalidCredentialsError (MF-1 / D1.3)."""
    store = FakeRedisPendingTotpLoginStore()
    user_repo = FakeUserRepository()

    user = make_user(has_totp=False)
    await user_repo.add(user)

    ticket = "login_scoped_ticket_to_enroll_resolver"
    await store.set_ticket(ticket, user.id, TicketScope.LOGIN)

    uc = ResolveEnrollmentTicket(pending_totp_store=store, user_repo=user_repo)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(ticket_id=ticket)


# ---------------------------------------------------------------------------
# Case 3: valid ticket, user already has TOTP enrolled (MF-4 invariant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_enrolled_user_raises_invalid_credentials() -> None:
    """M-2/Case-3: valid ENROLL ticket but user is already enrolled → InvalidCredentialsError.

    Enforces MF-4 / D5.3.1: post-resolution DB re-check. The use case must reject
    the ticket when the user's totp_secret_enc != TOTP_SENTINEL, with no DB mutation.
    """
    store = FakeRedisPendingTotpLoginStore()
    user_repo = FakeUserRepository()

    user = make_user(has_totp=True)  # totp_secret_enc != TOTP_SENTINEL
    await user_repo.add(user)

    ticket = "enrolled_user_ticket"
    await store.set_ticket(ticket, user.id, TicketScope.ENROLL)

    # Confirm user IS enrolled (pre-condition for this test)
    assert user.totp_secret_enc != TOTP_SENTINEL

    uc = ResolveEnrollmentTicket(pending_totp_store=store, user_repo=user_repo)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(ticket_id=ticket)

    # Ticket must NOT be consumed (use case raises before mutating state)
    still = await store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
    assert still == user.id, (
        "M-2/MF-4: ticket MUST NOT be consumed when user is already enrolled"
    )


# ---------------------------------------------------------------------------
# Case 4: valid ticket, user deleted (not found in DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_user_raises_invalid_credentials() -> None:
    """M-2/Case-4: ticket resolves to a user_id not in DB → InvalidCredentialsError."""
    store = FakeRedisPendingTotpLoginStore()
    user_repo = FakeUserRepository()  # empty — no users

    ghost_id = uuid.uuid4()
    ticket = "ticket_for_ghost_user"
    await store.set_ticket(ticket, ghost_id, TicketScope.ENROLL)

    uc = ResolveEnrollmentTicket(pending_totp_store=store, user_repo=user_repo)

    with pytest.raises(InvalidCredentialsError):
        await uc.execute(ticket_id=ticket)


# ---------------------------------------------------------------------------
# Case 5: happy path — valid ticket, unenrolled active user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_user_id() -> None:
    """M-2/Case-5: valid ENROLL ticket + unenrolled user → returns user_id."""
    store = FakeRedisPendingTotpLoginStore()
    user_repo = FakeUserRepository()

    user = make_user(has_totp=False)
    await user_repo.add(user)

    ticket = "happy_path_enroll_ticket"
    await store.set_ticket(ticket, user.id, TicketScope.ENROLL)

    uc = ResolveEnrollmentTicket(pending_totp_store=store, user_repo=user_repo)

    result = await uc.execute(ticket_id=ticket)

    assert result == user.id, (
        f"M-2/Case-5: execute must return the user_id, got {result!r} (expected {user.id!r})"
    )

    # Ticket must NOT be consumed by the resolver (it is consumed later by the
    # enrollment use case that calls executor — only once at the terminal step).
    still = await store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)
    assert still == user.id, (
        "M-2/Case-5: ResolveEnrollmentTicket must NOT consume the ticket — "
        "consumption is the responsibility of the terminal enrollment step"
    )
