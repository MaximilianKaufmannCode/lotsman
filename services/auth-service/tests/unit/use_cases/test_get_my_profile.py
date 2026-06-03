# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for GetMyProfile use case (self-service profile read)."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import GetMyProfileCommand
from auth_service.application.use_cases.get_my_profile import GetMyProfile
from auth_service.domain.errors import UserNotFoundError

from .conftest import FakeUserRepository, make_user


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def use_case(user_repo: FakeUserRepository) -> GetMyProfile:
    return GetMyProfile(user_repo=user_repo)


@pytest.mark.asyncio
async def test_returns_dto_for_existing_user(
    use_case: GetMyProfile,
    user_repo: FakeUserRepository,
) -> None:
    user = make_user(email="bob@example.com", role="editor")
    await user_repo.add(user)

    result = await use_case.execute(cmd=GetMyProfileCommand(actor_id=user.id))

    assert result.id == user.id
    assert result.email == user.email
    assert result.role == "editor"


@pytest.mark.asyncio
async def test_raises_not_found_for_unknown_user(use_case: GetMyProfile) -> None:
    with pytest.raises(UserNotFoundError):
        await use_case.execute(cmd=GetMyProfileCommand(actor_id=uuid.uuid4()))
