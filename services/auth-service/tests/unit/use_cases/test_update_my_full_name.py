# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for UpdateMyFullName use case (self-service profile edit)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import UpdateMyFullNameCommand
from auth_service.application.use_cases.update_my_full_name import UpdateMyFullName
from auth_service.domain.errors import ProfileValidationError, UserNotFoundError

from .conftest import FakeEventOutbox, FakeUserRepository, make_user


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(user_repo: FakeUserRepository, outbox: FakeEventOutbox) -> UpdateMyFullName:
    return UpdateMyFullName(user_repo=user_repo, outbox=outbox)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_updates_full_name_and_emits_event(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    user = make_user(email="alice@example.com")
    old_name = user.full_name
    await user_repo.add(user)

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name="Иван Тестовый Петров")
    )

    assert result.full_name == "Иван Тестовый Петров"
    stored = await user_repo.get_by_id(user.id)
    assert stored is not None
    assert stored.full_name == "Иван Тестовый Петров"

    assert "auth.user.profile_updated.v1" in outbox.event_types()

    # Verify event payload
    event = outbox.events[0]
    assert event.payload["field"] == "full_name"
    assert event.payload["before"] == old_name
    assert event.payload["after"] == "Иван Тестовый Петров"


@pytest.mark.asyncio
async def test_strips_surrounding_whitespace(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    user = make_user()
    await user_repo.add(user)

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name="  Петров Иван  ")
    )

    assert result.full_name == "Петров Иван"


# ---------------------------------------------------------------------------
# Validation failures — 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_string_raises_profile_validation_error(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user()
    await user_repo.add(user)

    with pytest.raises(ProfileValidationError):
        await use_case.execute(cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name=""))


@pytest.mark.asyncio
async def test_whitespace_only_raises_profile_validation_error(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user()
    await user_repo.add(user)

    with pytest.raises(ProfileValidationError):
        await use_case.execute(cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name="   "))


@pytest.mark.asyncio
async def test_over_200_chars_raises_profile_validation_error(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user()
    await user_repo.add(user)

    too_long = "А" * 201

    with pytest.raises(ProfileValidationError):
        await use_case.execute(cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name=too_long))


@pytest.mark.asyncio
async def test_exactly_200_chars_is_accepted(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user()
    await user_repo.add(user)

    max_name = "А" * 200

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name=max_name)
    )

    assert result.full_name == max_name


# ---------------------------------------------------------------------------
# User not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_user_raises_not_found(use_case: UpdateMyFullName) -> None:
    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=UpdateMyFullNameCommand(actor_id=uuid.uuid4(), full_name="Valid Name")
        )


# ---------------------------------------------------------------------------
# No-op when name is unchanged (still emits event — idempotency is OK here)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_change_still_calls_update(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    user = make_user()
    await user_repo.add(user)
    original_name = user.full_name

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name=original_name)
    )

    # Result is correct and event is still emitted (simple implementation).
    assert result.full_name == original_name
    assert "auth.user.profile_updated.v1" in outbox.event_types()
