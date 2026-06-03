# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for CreateAsset use case (US-13)."""

from __future__ import annotations

import uuid

import pytest

from registry_service.application.dto import CreateAssetCommand
from registry_service.application.use_cases.create_asset import CreateAsset
from registry_service.domain.entities import Asset
from registry_service.domain.errors import AssetAlreadyExistsError, InnInvalidError
from tests.unit.use_cases.fakes import FakeAssetRepository, FakeClock, FakeEventOutbox


@pytest.fixture
def repo() -> FakeAssetRepository:
    return FakeAssetRepository()


@pytest.fixture
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def use_case(repo: FakeAssetRepository, outbox: FakeEventOutbox, clock: FakeClock) -> CreateAsset:
    return CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_asset_happy_path(
    use_case: CreateAsset,
    repo: FakeAssetRepository,
    outbox: FakeEventOutbox,
) -> None:
    actor_id = uuid.uuid4()
    dto = await use_case.execute(
        cmd=CreateAssetCommand(
            name="ООО Новая Компания",
            inn="7707083893",
            notes="",
            actor_id=actor_id,
        )
    )

    assert dto.name == "ООО Новая Компания"
    assert dto.inn == "7707083893"
    assert dto.deleted_at is None

    # Verify persisted
    stored = await repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.name == "ООО Новая Компания"

    # Verify outbox event
    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.asset.created.v1"
    assert topic == "registry.assets"
    assert envelope.actor_id == actor_id


@pytest.mark.asyncio
async def test_create_asset_without_inn(use_case: CreateAsset) -> None:
    dto = await use_case.execute(
        cmd=CreateAssetCommand(
            name="ООО Без ИНН",
            inn=None,
            notes=None,
            actor_id=uuid.uuid4(),
        )
    )
    assert dto.inn is None


@pytest.mark.asyncio
async def test_create_asset_duplicate_name_raises(
    use_case: CreateAsset, repo: FakeAssetRepository
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    existing = Asset(
        id=uuid.uuid4(),
        name="ООО Ромашка",
        inn=None,
        notes=None,
        status="active",
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    await repo.add(existing)

    with pytest.raises(AssetAlreadyExistsError):
        await use_case.execute(
            cmd=CreateAssetCommand(
                name="ООО Ромашка",
                inn=None,
                notes=None,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_create_asset_invalid_inn_raises(use_case: CreateAsset) -> None:
    with pytest.raises(InnInvalidError):
        await use_case.execute(
            cmd=CreateAssetCommand(
                name="ООО Тест",
                inn="1234567890",  # invalid checksum
                notes=None,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_create_asset_archived_duplicate_allowed(
    use_case: CreateAsset, repo: FakeAssetRepository
) -> None:
    """Archived asset with same name does not block creation (partial unique index)."""
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    archived = Asset(
        id=uuid.uuid4(),
        name="ООО Ромашка",
        inn=None,
        notes=None,
        status="archived",
        created_at=now,
        updated_at=now,
        deleted_at=now,  # archived
    )
    await repo.add(archived)

    # Should succeed — the archived row does not count
    dto = await use_case.execute(
        cmd=CreateAssetCommand(
            name="ООО Ромашка",
            inn=None,
            notes=None,
            actor_id=uuid.uuid4(),
        )
    )
    assert dto.name == "ООО Ромашка"
