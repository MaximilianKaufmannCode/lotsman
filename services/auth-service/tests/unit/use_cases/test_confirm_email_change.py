# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ConfirmEmailChange use case."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import ConfirmEmailChangeCommand
from auth_service.application.use_cases.confirm_email_change import ConfirmEmailChange
from auth_service.domain.errors import (
    EmailAlreadyTakenError,
    EmailChangeRequestNotFoundError,
    EmailChangeVerificationFailedError,
    EmailChangeVerificationFailedLastError,
)

from .conftest import (
    FakeEventOutbox,
    FakePasswordHasher,
    FakeUserRepository,
    make_user,
)

# ---------------------------------------------------------------------------
# Fake email change store
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_use_case(
    user_repo: FakeUserRepository,
    store: FakeEmailChangeStore,
) -> tuple[ConfirmEmailChange, FakeEventOutbox]:
    outbox = FakeEventOutbox()
    uc = ConfirmEmailChange(
        user_repo=user_repo,
        email_change_store=store,
        hasher=FakePasswordHasher(),
        outbox=outbox,
    )
    return uc, outbox


async def _seed_request(
    store: FakeEmailChangeStore,
    *,
    user_id: uuid.UUID,
    new_email: str = "new@example.com",
    code: str = "12345678",
    attempts: int = 3,
) -> str:
    request_id = str(uuid.uuid4())
    # FakePasswordHasher stores as "HASH:<code>"
    await store.set_request(
        request_id,
        user_id=user_id,
        new_email=new_email,
        code_hash=f"HASH:{code}",
        attempts_remaining=attempts,
    )
    return request_id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_updates_email_and_emits_event() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)

    store = FakeEmailChangeStore()
    request_id = await _seed_request(
        store, user_id=user.id, new_email="new@example.com", code="12345678"
    )

    uc, outbox = _make_use_case(repo, store)
    dto = await uc.execute(
        cmd=ConfirmEmailChangeCommand(
            actor_id=user.id,
            request_id=request_id,
            verification_code="12345678",
        )
    )

    assert dto.email == "new@example.com"

    # User entity updated
    updated = await repo.get_by_id(user.id)
    assert updated is not None
    assert updated.email == "new@example.com"

    # Audit event emitted
    assert "auth.user.email_changed.v1" in outbox.event_types()

    # Redis row deleted
    assert await store.get_request(request_id) is None


# ---------------------------------------------------------------------------
# Request not found / mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_request_id_raises_not_found() -> None:
    user = make_user()
    repo = FakeUserRepository()
    await repo.add(user)
    store = FakeEmailChangeStore()
    uc, _ = _make_use_case(repo, store)

    with pytest.raises(EmailChangeRequestNotFoundError):
        await uc.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=user.id,
                request_id="nonexistent",
                verification_code="12345678",
            )
        )


@pytest.mark.asyncio
async def test_different_actor_raises_not_found() -> None:
    alice = make_user(email="alice@example.com")
    bob = make_user(email="bob@example.com")
    repo = FakeUserRepository()
    await repo.add(alice)
    await repo.add(bob)

    store = FakeEmailChangeStore()
    # Request belongs to alice
    request_id = await _seed_request(store, user_id=alice.id)

    uc, _ = _make_use_case(repo, store)

    # Bob tries to confirm alice's request
    with pytest.raises(EmailChangeRequestNotFoundError):
        await uc.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=bob.id,
                request_id=request_id,
                verification_code="12345678",
            )
        )


# ---------------------------------------------------------------------------
# Wrong code / attempt exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_code_returns_attempts_remaining() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)

    store = FakeEmailChangeStore()
    request_id = await _seed_request(store, user_id=user.id, code="12345678", attempts=3)

    uc, _ = _make_use_case(repo, store)

    with pytest.raises(EmailChangeVerificationFailedError) as exc_info:
        await uc.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=user.id,
                request_id=request_id,
                verification_code="00000000",  # wrong
            )
        )

    assert exc_info.value.attempts_remaining == 2


@pytest.mark.asyncio
async def test_last_wrong_code_deletes_request_and_raises_last_error() -> None:
    user = make_user(email="alice@example.com")
    repo = FakeUserRepository()
    await repo.add(user)

    store = FakeEmailChangeStore()
    request_id = await _seed_request(store, user_id=user.id, code="12345678", attempts=1)

    uc, _ = _make_use_case(repo, store)

    with pytest.raises(EmailChangeVerificationFailedLastError):
        await uc.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=user.id,
                request_id=request_id,
                verification_code="00000000",  # wrong
            )
        )

    # Request should be deleted
    assert await store.get_request(request_id) is None


# ---------------------------------------------------------------------------
# Race protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_condition_email_taken_by_another_user_raises_conflict() -> None:
    alice = make_user(email="alice@example.com")
    eve = make_user(email="eve@example.com")  # will claim new email during race
    repo = FakeUserRepository()
    await repo.add(alice)
    await repo.add(eve)

    store = FakeEmailChangeStore()
    # alice requested to change to charlie@example.com
    request_id = await _seed_request(
        store, user_id=alice.id, new_email="charlie@example.com", code="99999999"
    )

    # Simulate race: charlie@example.com is now taken by eve
    charlie = make_user(email="charlie@example.com")
    await repo.add(charlie)

    uc, _ = _make_use_case(repo, store)

    with pytest.raises(EmailAlreadyTakenError):
        await uc.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=alice.id,
                request_id=request_id,
                verification_code="99999999",
            )
        )

    # Request should be cleaned up
    assert await store.get_request(request_id) is None
