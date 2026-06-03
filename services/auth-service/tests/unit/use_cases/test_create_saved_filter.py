# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for CreateSavedFilter use case (v1.23.0 — registry-filters feature).

Covers the critical business rules:
  1. filter_json must be a dict.
  2. Maximum 20 presets per user.
  3. Unique name per user.
  4. is_default=True unsets the previous default.
  5. Audit event emitted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from auth_service.application.dto import CreateSavedFilterCommand, SavedFilterDTO
from auth_service.application.use_cases.create_saved_filter import (
    _MAX_PRESETS_PER_USER,
    CreateSavedFilter,
)
from auth_service.domain.entities import SavedFilter
from auth_service.domain.errors import (
    SavedFilterJsonInvalidError,
    SavedFilterLimitExceededError,
    SavedFilterNameTakenError,
)
from .conftest import FakeEventOutbox

# ---------------------------------------------------------------------------
# Fake SavedFilterRepository
# ---------------------------------------------------------------------------


class FakeSavedFilterRepository:
    def __init__(self, presets: list[SavedFilter] | None = None) -> None:
        self._store: dict[uuid.UUID, SavedFilter] = {p.id: p for p in (presets or [])}

    async def list_for_user(self, user_id: uuid.UUID) -> list[SavedFilter]:
        return sorted(
            [p for p in self._store.values() if p.user_id == user_id],
            key=lambda p: (-p.is_default, p.name),
        )

    async def get_by_id(self, filter_id: uuid.UUID, user_id: uuid.UUID) -> SavedFilter | None:
        p = self._store.get(filter_id)
        if p and p.user_id == user_id:
            return p
        return None

    async def name_exists(self, user_id: uuid.UUID, name: str) -> bool:
        return any(
            p.user_id == user_id and p.name == name for p in self._store.values()
        )

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        return sum(1 for p in self._store.values() if p.user_id == user_id)

    async def add(self, saved_filter: SavedFilter) -> None:
        self._store[saved_filter.id] = saved_filter

    async def update(self, saved_filter: SavedFilter) -> None:
        self._store[saved_filter.id] = saved_filter

    async def delete(self, filter_id: uuid.UUID) -> None:
        self._store.pop(filter_id, None)

    async def unset_default_for_user(self, user_id: uuid.UUID) -> None:
        for p in self._store.values():
            if p.user_id == user_id and p.is_default:
                p.is_default = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preset(user_id: uuid.UUID, name: str = "My Filter") -> SavedFilter:
    now = datetime.now(tz=UTC)
    return SavedFilter(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        filter_json={"v": 1},
        is_default=False,
        created_at=now,
        updated_at=now,
    )


def _make_cmd(
    user_id: uuid.UUID,
    *,
    name: str = "Weekly View",
    filter_json: Any = None,
    is_default: bool = False,
) -> CreateSavedFilterCommand:
    return CreateSavedFilterCommand(
        user_id=user_id,
        name=name,
        filter_json=filter_json if filter_json is not None else {"type": "contract"},
        is_default=is_default,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_saved_filter_happy_path() -> None:
    """Happy path: create a new preset and get an audit event."""
    user_id = uuid.uuid4()
    repo = FakeSavedFilterRepository()
    outbox = FakeEventOutbox()
    uc = CreateSavedFilter(repo=repo, outbox=outbox)  # type: ignore[arg-type]

    dto = await uc.execute(cmd=_make_cmd(user_id, name="Q1 Contracts"))

    assert isinstance(dto, SavedFilterDTO)
    assert dto.name == "Q1 Contracts"
    assert dto.user_id == user_id
    assert dto.is_default is False
    assert dto.filter_json == {"type": "contract"}

    # Audit event must be emitted
    assert len(outbox.events) == 1
    assert outbox.events[0].type == "auth.user.filter_preset_saved.v1"


@pytest.mark.asyncio
async def test_create_saved_filter_non_dict_json_raises() -> None:
    """Use-case guard: filter_json that is not a dict raises SavedFilterJsonInvalidError.

    In normal API flow Pydantic rejects non-dicts at the DTO boundary before the
    use case is called. This test bypasses Pydantic to verify the use-case guard
    directly (defence-in-depth for programmatic callers).
    """
    user_id = uuid.uuid4()
    repo = FakeSavedFilterRepository()
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    # Construct command bypassing Pydantic validation (model_construct skips validators)
    cmd = CreateSavedFilterCommand.model_construct(
        user_id=user_id,
        name="Bad",
        filter_json=["not", "a", "dict"],  # type: ignore[arg-type]
        is_default=False,
    )
    with pytest.raises(SavedFilterJsonInvalidError):
        await uc.execute(cmd=cmd)


@pytest.mark.asyncio
async def test_create_saved_filter_null_json_raises() -> None:
    """Use-case guard: filter_json=None raises SavedFilterJsonInvalidError.

    Bypasses Pydantic validation (model_construct) to test the use-case guard directly.
    """
    user_id = uuid.uuid4()
    repo = FakeSavedFilterRepository()
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    cmd = CreateSavedFilterCommand.model_construct(
        user_id=user_id,
        name="NullJson",
        filter_json=None,  # type: ignore[arg-type]
        is_default=False,
    )
    with pytest.raises(SavedFilterJsonInvalidError):
        await uc.execute(cmd=cmd)


@pytest.mark.asyncio
async def test_create_saved_filter_limit_exceeded_raises() -> None:
    """Creating the 21st preset raises SavedFilterLimitExceededError."""
    user_id = uuid.uuid4()
    presets = [_make_preset(user_id, name=f"Filter {i}") for i in range(_MAX_PRESETS_PER_USER)]
    repo = FakeSavedFilterRepository(presets)
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    with pytest.raises(SavedFilterLimitExceededError):
        await uc.execute(cmd=_make_cmd(user_id, name="One More"))


@pytest.mark.asyncio
async def test_create_saved_filter_exactly_at_limit_succeeds() -> None:
    """Creating the 20th preset (at the limit) succeeds."""
    user_id = uuid.uuid4()
    presets = [_make_preset(user_id, name=f"Filter {i}") for i in range(_MAX_PRESETS_PER_USER - 1)]
    repo = FakeSavedFilterRepository(presets)
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    dto = await uc.execute(cmd=_make_cmd(user_id, name="The 20th"))
    assert dto.name == "The 20th"


@pytest.mark.asyncio
async def test_create_saved_filter_duplicate_name_raises() -> None:
    """Creating a preset with a name already used by this user raises SavedFilterNameTakenError."""
    user_id = uuid.uuid4()
    existing = _make_preset(user_id, name="My Filter")
    repo = FakeSavedFilterRepository([existing])
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    with pytest.raises(SavedFilterNameTakenError):
        await uc.execute(cmd=_make_cmd(user_id, name="My Filter"))


@pytest.mark.asyncio
async def test_create_saved_filter_same_name_different_user_ok() -> None:
    """Same name for a different user must not be blocked."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    existing = _make_preset(user_a, name="Shared Name")
    repo = FakeSavedFilterRepository([existing])
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    # Should not raise for user_b
    dto = await uc.execute(cmd=_make_cmd(user_b, name="Shared Name"))
    assert dto.user_id == user_b


@pytest.mark.asyncio
async def test_create_saved_filter_is_default_unsets_previous() -> None:
    """Setting is_default=True must unset the previous default for the same user."""
    user_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    old_default = SavedFilter(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Old Default",
        filter_json={"v": 1},
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    repo = FakeSavedFilterRepository([old_default])
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    new_dto = await uc.execute(cmd=_make_cmd(user_id, name="New Default", is_default=True))

    assert new_dto.is_default is True

    # The old default must have been unset by the repo
    old_in_store = await repo.get_by_id(old_default.id, user_id)
    assert old_in_store is not None
    assert old_in_store.is_default is False


@pytest.mark.asyncio
async def test_create_saved_filter_is_default_false_leaves_existing_default() -> None:
    """is_default=False does not disturb an existing default preset."""
    user_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    existing_default = SavedFilter(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Current Default",
        filter_json={"v": 1},
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    repo = FakeSavedFilterRepository([existing_default])
    uc = CreateSavedFilter(repo=repo, outbox=FakeEventOutbox())  # type: ignore[arg-type]

    await uc.execute(cmd=_make_cmd(user_id, name="Regular Preset", is_default=False))

    still_default = await repo.get_by_id(existing_default.id, user_id)
    assert still_default is not None
    assert still_default.is_default is True
