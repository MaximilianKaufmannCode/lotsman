# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for CreateUser use case (US-17)."""

from __future__ import annotations

import pytest

from auth_service.application.dto import CreateUserCommand
from auth_service.application.use_cases.create_user import CreateUser
from auth_service.domain.errors import UserAlreadyExistsError

from .conftest import FakeEventOutbox, FakePasswordHasher, FakeUserRepository, make_user


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture()
def use_case(
    user_repo: FakeUserRepository,
    hasher: FakePasswordHasher,
    outbox: FakeEventOutbox,
) -> CreateUser:
    return CreateUser(user_repo=user_repo, hasher=hasher, outbox=outbox)


def _make_cmd(
    *,
    email: str = "alice@example.com",
    full_name: str = "Alice Doe",
    role: str = "editor",
    actor_id=None,
) -> CreateUserCommand:
    import uuid

    return CreateUserCommand(
        email=email,
        full_name=full_name,
        role=role,
        actor_id=actor_id or uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_creates_user_successfully(
    use_case: CreateUser,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    cmd = _make_cmd(email="alice@example.com", role="viewer")
    result = await use_case.execute(cmd=cmd)

    assert result.user_id is not None
    assert len(result.oob_otp) > 0

    # Persisted in repo
    stored = await user_repo.get_by_id(result.user_id)
    assert stored is not None
    assert stored.email == "alice@example.com"
    assert stored.role == "viewer"
    assert stored.must_change_password is True
    # Password hash is argon2id of the OOB OTP
    assert stored.password_hash.startswith("HASH:")

    # Event emitted
    assert "auth.user.created.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_email_is_lowercased(
    use_case: CreateUser,
    user_repo: FakeUserRepository,
) -> None:
    cmd = _make_cmd(email="BOB@EXAMPLE.COM")
    result = await use_case.execute(cmd=cmd)
    stored = await user_repo.get_by_id(result.user_id)
    assert stored is not None
    assert stored.email == "bob@example.com"


@pytest.mark.asyncio
async def test_duplicate_email_raises_already_exists(
    use_case: CreateUser,
    user_repo: FakeUserRepository,
) -> None:
    existing = make_user(email="alice@example.com")
    await user_repo.add(existing)

    cmd = _make_cmd(email="alice@example.com")
    with pytest.raises(UserAlreadyExistsError):
        await use_case.execute(cmd=cmd)


@pytest.mark.asyncio
async def test_oob_otp_different_each_call(use_case: CreateUser) -> None:
    cmd1 = _make_cmd(email="user1@example.com")
    cmd2 = _make_cmd(email="user2@example.com")
    r1 = await use_case.execute(cmd=cmd1)
    r2 = await use_case.execute(cmd=cmd2)
    assert r1.oob_otp != r2.oob_otp


@pytest.mark.asyncio
async def test_no_event_stored_in_db_on_duplicate(
    use_case: CreateUser,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    existing = make_user(email="alice@example.com")
    await user_repo.add(existing)

    cmd = _make_cmd(email="alice@example.com")
    with pytest.raises(UserAlreadyExistsError):
        await use_case.execute(cmd=cmd)

    assert outbox.events == []
