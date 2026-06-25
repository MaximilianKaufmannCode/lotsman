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
# No-op when name is unchanged — update still runs, but NO profile_updated event
# is emitted (avoids outbox spam when only the font-size preference is PATCHed,
# since the SPA always re-sends the current full_name alongside it).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_name_change_does_not_emit_event(
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

    # Result is correct and the row is still persisted (idempotent save), but the
    # identity-change event is NOT emitted because nothing identity-relevant changed.
    assert result.full_name == original_name
    assert "auth.user.profile_updated.v1" not in outbox.event_types()


# ---------------------------------------------------------------------------
# ui_font_scale — optional self-service UI preference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_font_scale_only_change_persists_without_name_event(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    user = make_user()
    await user_repo.add(user)
    original_name = user.full_name

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(
            actor_id=user.id, full_name=original_name, ui_font_scale=130
        )
    )

    # Preference persisted, returned in the DTO, and NO name-change event emitted.
    assert result.ui_font_scale == 130
    stored = await user_repo.get_by_id(user.id)
    assert stored is not None
    assert stored.ui_font_scale == 130
    assert "auth.user.profile_updated.v1" not in outbox.event_types()


@pytest.mark.asyncio
async def test_name_and_font_change_emits_name_event(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    outbox: FakeEventOutbox,
) -> None:
    user = make_user()
    await user_repo.add(user)

    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(
            actor_id=user.id, full_name="Новое Имя", ui_font_scale=115
        )
    )

    assert result.full_name == "Новое Имя"
    assert result.ui_font_scale == 115
    assert "auth.user.profile_updated.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_default_when_font_scale_omitted(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user()
    await user_repo.add(user)

    # Omitting ui_font_scale must leave the existing value untouched (default 100).
    result = await use_case.execute(
        cmd=UpdateMyFullNameCommand(actor_id=user.id, full_name="Другое Имя")
    )

    assert result.ui_font_scale == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_scale", [79, 151, 0, 1000, -5])
async def test_out_of_range_font_scale_raises(
    use_case: UpdateMyFullName,
    user_repo: FakeUserRepository,
    bad_scale: int,
) -> None:
    user = make_user()
    await user_repo.add(user)

    with pytest.raises(ProfileValidationError):
        await use_case.execute(
            cmd=UpdateMyFullNameCommand(
                actor_id=user.id, full_name="Valid Name", ui_font_scale=bad_scale
            )
        )
