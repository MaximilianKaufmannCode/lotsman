# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for ChangeAssetStatus use case (v1.23.0 — registry-filters feature)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import ChangeAssetStatusCommand
from registry_service.application.use_cases.change_asset_status import ChangeAssetStatus
from registry_service.domain.entities import Asset
from registry_service.domain.errors import AssetNotFoundError, InvalidAssetStatusError
from tests.unit.use_cases.fakes import FakeAssetRepository, FakeClock, FakeEventOutbox


def _make_asset(
    *,
    status: str = "active",
    deleted_at: datetime | None = None,
) -> Asset:
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


def _make_cmd(asset_id: uuid.UUID, status: str) -> ChangeAssetStatusCommand:
    return ChangeAssetStatusCommand(
        asset_id=asset_id,
        status=status,
        actor_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_active_to_liquidating_happy_path() -> None:
    """active → liquidating: deleted_at stays None, status changes, event emitted."""
    asset = _make_asset(status="active")
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    uc = ChangeAssetStatus(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto, cascaded = await uc.execute(cmd=_make_cmd(asset.id, "liquidating"))

    assert dto.status == "liquidating"
    assert dto.deleted_at is None
    assert cascaded == 0

    stored = await repo.get_by_id(asset.id)
    assert stored is not None
    assert stored.status == "liquidating"
    assert stored.deleted_at is None

    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.asset.status_changed.v1"
    assert topic == "registry.assets"
    assert envelope.payload["before"] == "active"
    assert envelope.payload["after"] == "liquidating"
    assert envelope.payload["cascaded_document_count"] == 0


@pytest.mark.asyncio
async def test_active_to_archived_sets_deleted_at() -> None:
    """active → archived: deleted_at becomes set (dual-signal model)."""
    asset = _make_asset(status="active")
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    clock = FakeClock()
    uc = ChangeAssetStatus(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    dto, cascaded = await uc.execute(cmd=_make_cmd(asset.id, "archived"))

    assert dto.status == "archived"
    assert dto.deleted_at is not None
    assert cascaded == 0  # FakeAssetRepository.archive_cascade_documents returns 0

    stored = await repo.get_by_id(asset.id)
    assert stored is not None
    assert stored.deleted_at == clock.now()


@pytest.mark.asyncio
async def test_archived_to_active_clears_deleted_at() -> None:
    """archived → active: deleted_at is cleared (restore path)."""
    now = datetime.now(tz=UTC)
    asset = _make_asset(status="archived", deleted_at=now)
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    uc = ChangeAssetStatus(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto, cascaded = await uc.execute(cmd=_make_cmd(asset.id, "active"))

    assert dto.status == "active"
    assert dto.deleted_at is None
    assert cascaded == 0

    stored = await repo.get_by_id(asset.id)
    assert stored is not None
    assert stored.deleted_at is None


@pytest.mark.asyncio
async def test_invalid_status_raises() -> None:
    """Requesting an unknown status raises InvalidAssetStatusError."""
    asset = _make_asset()
    repo = FakeAssetRepository([asset])
    uc = ChangeAssetStatus(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(InvalidAssetStatusError):
        await uc.execute(cmd=_make_cmd(asset.id, "dissolved"))


@pytest.mark.asyncio
async def test_nonexistent_asset_raises() -> None:
    """Asset not in store raises AssetNotFoundError."""
    repo = FakeAssetRepository()
    uc = ChangeAssetStatus(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(AssetNotFoundError):
        await uc.execute(cmd=_make_cmd(uuid.uuid4(), "liquidating"))


@pytest.mark.asyncio
async def test_event_payload_carries_before_and_after() -> None:
    """Event payload must include before/after status for audit trail."""
    asset = _make_asset(status="liquidating")
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    uc = ChangeAssetStatus(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    await uc.execute(cmd=_make_cmd(asset.id, "archived"))

    envelope, _ = outbox.published[0]
    assert envelope.payload["before"] == "liquidating"
    assert envelope.payload["after"] == "archived"


@pytest.mark.asyncio
async def test_same_status_transition_is_idempotent() -> None:
    """active → active: no error, event still emitted (allows client retries)."""
    asset = _make_asset(status="active")
    repo = FakeAssetRepository([asset])
    outbox = FakeEventOutbox()
    uc = ChangeAssetStatus(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto, cascaded = await uc.execute(cmd=_make_cmd(asset.id, "active"))

    assert dto.status == "active"
    assert dto.deleted_at is None
    assert cascaded == 0
    assert len(outbox.published) == 1
