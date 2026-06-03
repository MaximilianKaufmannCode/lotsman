# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for ArchiveAsset use case (US-15)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.use_cases.archive_asset import ArchiveAsset
from registry_service.domain.entities import Asset
from registry_service.domain.errors import AssetNotFoundError
from tests.unit.use_cases.fakes import FakeAssetRepository, FakeClock, FakeEventOutbox


def _make_asset(*, deleted_at: datetime | None = None, status: str = "active") -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=uuid.uuid4(),
        name="ООО Тест",
        inn=None,
        notes=None,
        status=status,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )


@pytest.mark.asyncio
async def test_archive_asset_happy_path() -> None:
    """Archive an active asset → asset.deleted_at set, event emitted."""
    asset = _make_asset()
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    use_case = ArchiveAsset(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    cascaded = await use_case.execute(asset_id=asset.id, actor_id=uuid.uuid4())

    assert cascaded == 0  # FakeAssetRepository.archive_cascade_documents always returns 0

    stored = await repo.get_by_id(asset.id)
    assert stored is not None
    assert stored.deleted_at is not None

    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    # ArchiveAsset delegates to ChangeAssetStatus which emits status_changed.v1
    assert envelope.type == "registry.asset.status_changed.v1"
    assert topic == "registry.assets"


@pytest.mark.asyncio
async def test_archive_nonexistent_asset_raises() -> None:
    repo = FakeAssetRepository()
    use_case = ArchiveAsset(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(AssetNotFoundError):
        await use_case.execute(asset_id=uuid.uuid4(), actor_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_archive_asset_event_contains_cascade_count() -> None:
    """The outbox event must include cascaded_document_count."""
    asset = _make_asset()
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    use_case = ArchiveAsset(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(asset_id=asset.id, actor_id=uuid.uuid4())

    envelope, _ = outbox.published[0]
    # The event payload should carry the cascade count
    assert "cascaded_document_count" in envelope.payload
    assert envelope.payload["cascaded_document_count"] == 0


@pytest.mark.asyncio
async def test_archive_already_archived_asset_succeeds() -> None:
    """Archiving an already-archived asset: does not raise, re-sets deleted_at."""
    asset = _make_asset(deleted_at=datetime.now(tz=UTC))
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    use_case = ArchiveAsset(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    # Should not raise — asset exists in store regardless of deleted_at
    await use_case.execute(asset_id=asset.id, actor_id=uuid.uuid4())

    # Event still emitted (archive is not idempotent for assets at the use case level)
    assert len(outbox.published) == 1
