# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RequestEmailChange use case."""

from __future__ import annotations

import pytest

from auth_service.application.dto import RequestEmailChangeCommand
from auth_service.application.use_cases.request_email_change import RequestEmailChange
from auth_service.domain.errors import (
    EmailAlreadyTakenError,
    EmailChannelNotConfiguredError,
    EmailSameAsCurrentError,
    EmailValidationError,
    UserNotFoundError,
)

from .conftest import (
    FakeEventOutbox,
    FakePasswordHasher,
    FakeUserRepository,
    make_user,
)

# ---------------------------------------------------------------------------
# Fake helpers for this test file
# ---------------------------------------------------------------------------


class FakeEmailChangeStore:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    async def set_request(self, request_id, *, user_id, new_email, code_hash, attempts_remaining):
        self._store[request_id] = {
            "user_id": str(user_id),
            "new_email": new_email,
            "code_hash": code_hash,
            "attempts_remaining": attempts_remaining,
        }

    async def get_request(self, request_id):
        return self._store.get(request_id)

    async def delete_request(self, request_id):
        self._store.pop(request_id, None)

    async def decrement_attempts(self, request_id):
        row = self._store.get(request_id)
        if row is None:
            return 0
        row["attempts_remaining"] = max(0, row["attempts_remaining"] - 1)
        return row["attempts_remaining"]


class FakeEmailSender:
    def __init__(self, *, fail: bool = False, not_configured: bool = False) -> None:
        self.sent: list[dict] = []
        self._fail = fail
        self._not_configured = not_configured

    async def send(self, *, to: str, subject: str, body_text: str) -> None:
        if self._not_configured:
            raise EmailChannelNotConfiguredError()
        if self._fail:
            raise RuntimeError("SMTP error")
        self.sent.append({"to": to, "subject": subject, "body_text": body_text})


def _make_use_case(
    *,
    user_repo: FakeUserRepository | None = None,
    email_sender: FakeEmailSender | None = None,
) -> tuple[RequestEmailChange, FakeUserRepository, FakeEmailChangeStore, FakeEventOutbox]:
    repo = user_repo or FakeUserRepository()
    store = FakeEmailChangeStore()
    hasher = FakePasswordHasher()
    sender = email_sender or FakeEmailSender()
    outbox = FakeEventOutbox()
    uc = RequestEmailChange(
        user_repo=repo,
        email_change_store=store,
        hasher=hasher,
        email_sender=sender,
        outbox=outbox,
    )
    return uc, repo, store, outbox


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_stores_request_and_sends_email() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    sender = FakeEmailSender()
    uc, _, store, outbox = _make_use_case(user_repo=repo, email_sender=sender)

    dto = await uc.execute(
        cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="alice-new@example.com")
    )

    assert dto.code_ttl_seconds == 900
    assert "alice" in dto.masked_new_email or "***" in dto.masked_new_email
    assert len(store._store) == 1

    # Email was sent to new address
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == "alice-new@example.com"
    assert "Лоцман" in sender.sent[0]["subject"]

    # Audit event emitted
    assert "auth.user.email_change_requested.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_new_email_is_normalised_to_lowercase() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    sender = FakeEmailSender()
    uc, _, store, _ = _make_use_case(user_repo=repo, email_sender=sender)

    await uc.execute(
        cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="ALICE-NEW@EXAMPLE.COM")
    )

    row = list(store._store.values())[0]
    assert row["new_email"] == "alice-new@example.com"
    assert sender.sent[0]["to"] == "alice-new@example.com"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_email_format_raises_validation_error() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    uc, _, _, _ = _make_use_case(user_repo=repo)

    with pytest.raises(EmailValidationError):
        await uc.execute(cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="not-an-email"))


@pytest.mark.asyncio
async def test_same_email_raises_same_as_current() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    uc, _, _, _ = _make_use_case(user_repo=repo)

    with pytest.raises(EmailSameAsCurrentError):
        await uc.execute(
            cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="alice@example.com")
        )


@pytest.mark.asyncio
async def test_same_email_case_insensitive() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    uc, _, _, _ = _make_use_case(user_repo=repo)

    with pytest.raises(EmailSameAsCurrentError):
        await uc.execute(
            cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="ALICE@EXAMPLE.COM")
        )


@pytest.mark.asyncio
async def test_taken_email_raises_already_taken() -> None:
    alice = make_user(email="alice@example.com")
    bob = make_user(email="bob@example.com")
    repo = FakeUserRepository()
    await repo.add(alice)
    await repo.add(bob)
    uc, _, _, _ = _make_use_case(user_repo=repo)

    with pytest.raises(EmailAlreadyTakenError):
        await uc.execute(
            cmd=RequestEmailChangeCommand(actor_id=alice.id, new_email="bob@example.com")
        )


@pytest.mark.asyncio
async def test_inactive_taken_email_is_allowed() -> None:
    """A deactivated user's email should not block the change."""
    alice = make_user(email="alice@example.com")
    inactive = make_user(email="bob@example.com", is_active=False)
    repo = FakeUserRepository()
    await repo.add(alice)
    await repo.add(inactive)
    sender = FakeEmailSender()
    uc, _, _, _ = _make_use_case(user_repo=repo, email_sender=sender)

    # Should succeed — inactive user's email is not taken
    dto = await uc.execute(
        cmd=RequestEmailChangeCommand(actor_id=alice.id, new_email="bob@example.com")
    )
    assert dto.request_id


@pytest.mark.asyncio
async def test_user_not_found_raises_error() -> None:
    import uuid

    repo = FakeUserRepository()
    uc, _, _, _ = _make_use_case(user_repo=repo)

    with pytest.raises(UserNotFoundError):
        await uc.execute(
            cmd=RequestEmailChangeCommand(actor_id=uuid.uuid4(), new_email="new@example.com")
        )


# ---------------------------------------------------------------------------
# Email channel errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_not_configured_raises_503_domain_error() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)
    sender = FakeEmailSender(not_configured=True)
    uc, _, _, _ = _make_use_case(user_repo=repo, email_sender=sender)

    with pytest.raises(EmailChannelNotConfiguredError):
        await uc.execute(
            cmd=RequestEmailChangeCommand(actor_id=user.id, new_email="new@example.com")
        )


# ---------------------------------------------------------------------------
# Masking helper
# ---------------------------------------------------------------------------


def test_masked_email_never_reveals_full_address() -> None:
    from auth_service.application.use_cases.request_email_change import _mask_email

    masked = _mask_email("alice@example.com")
    assert "***" in masked
    assert "alice" not in masked or masked != "alice@example.com"


def test_masked_email_contains_at_sign() -> None:
    from auth_service.application.use_cases.request_email_change import _mask_email

    masked = _mask_email("fa_l@dho.ru")
    assert "@" in masked
