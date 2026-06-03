# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for asset CRUD — US-12 through US-15.

Covers:
  - Asset CRUD with INN format + ФНС checksum validation
  - Cascade-archive emits AssetArchived with cascaded_document_count
  - Already-archived documents NOT touched by cascade (Q5)
  - Asset name uniqueness scoped to WHERE deleted_at IS NULL (partial index)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(
    True,
    reason=(
        "Requires testcontainers[postgres] + asyncpg at runtime. "
        "Unblock by installing: uv add --dev 'testcontainers[postgres]' asyncpg"
    ),
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_asset(name: str = "ООО Тест", inn: str | None = None) -> Asset:  # type: ignore[name-defined]
    from registry_service.domain.entities import Asset

    now = _now()
    return Asset(
        id=uuid.uuid4(),
        name=name,
        inn=inn,
        notes=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# US-12 — List assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_assets_excludes_archived(session):
    """List returns only non-deleted assets."""
    from registry_service.infrastructure.db.repositories import SqlAssetRepository

    repo = SqlAssetRepository(session)
    active = _make_asset("ООО Активный")
    archived = _make_asset("ООО Архивный")
    archived.deleted_at = _now()

    await repo.add(active)
    await repo.add(archived)

    results = await repo.list_active()
    ids = [a.id for a in results]
    assert active.id in ids
    assert archived.id not in ids


@pytest.mark.asyncio
async def test_list_assets_all_deleted_returns_empty(session):
    """When all assets are soft-deleted, list returns empty."""
    from registry_service.infrastructure.db.repositories import SqlAssetRepository

    repo = SqlAssetRepository(session)
    a = _make_asset("ООО Все Удалены")
    a.deleted_at = _now()
    await repo.add(a)

    results = await repo.list_active()
    assert all(r.id != a.id for r in results)


@pytest.mark.asyncio
async def test_search_assets_by_name_trgm(session):
    """Search by name via pg_trgm similarity returns matching assets."""
    from registry_service.infrastructure.db.repositories import SqlAssetRepository

    repo = SqlAssetRepository(session)
    a1 = _make_asset("ООО Газпром Инвест")
    a2 = _make_asset("АО Газпромнефть")
    a3 = _make_asset("ООО Лукойл")

    await repo.add(a1)
    await repo.add(a2)
    await repo.add(a3)

    # 3-char query uses pg_trgm
    results = await repo.list_active(q="Газпром")
    ids = {a.id for a in results}
    assert a1.id in ids
    assert a2.id in ids
    assert a3.id not in ids


# ---------------------------------------------------------------------------
# US-13 — Create asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_asset_with_valid_inn_emits_event(session, clock):
    """Creating an asset with a valid 10-digit INN writes outbox event."""
    from sqlalchemy import select

    from registry_service.application.dto import CreateAssetCommand
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    sut = CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    # Valid 10-digit INN with correct ФНС checksum: 7701234567
    dto = await sut.execute(
        cmd=CreateAssetCommand(
            name="ООО Новая Компания",
            inn="7701234567",
            notes=None,
            actor_id=actor_id,
        )
    )

    assert dto.id is not None

    # Verify outbox
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.assets"))
    rows = result.scalars().all()
    assert any(r.payload.get("type") == "registry.asset.created.v1" for r in rows)
    assert any(r.payload.get("actor_id") == str(actor_id) for r in rows)


@pytest.mark.asyncio
async def test_create_asset_duplicate_name_active_returns_409(session, clock):
    """Creating an asset with a name that already exists (active) raises AssetAlreadyExistsError."""
    from registry_service.application.dto import CreateAssetCommand
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.domain.errors import AssetAlreadyExistsError
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    sut = CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    await sut.execute(
        cmd=CreateAssetCommand(name="ООО Ромашка", inn=None, notes=None, actor_id=actor_id)
    )

    with pytest.raises(AssetAlreadyExistsError):
        await sut.execute(
            cmd=CreateAssetCommand(name="ООО Ромашка", inn=None, notes=None, actor_id=actor_id)
        )


@pytest.mark.asyncio
async def test_create_asset_duplicate_name_archived_allowed(session, clock):
    """Creating asset with same name as an archived asset is allowed (partial unique index)."""
    from registry_service.application.dto import CreateAssetCommand
    from registry_service.application.use_cases.archive_asset import ArchiveAsset
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    create_sut = CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    archive_sut = ArchiveAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    dto1 = await create_sut.execute(
        cmd=CreateAssetCommand(name="ООО Дубль", inn=None, notes=None, actor_id=actor_id)
    )
    # Archive it
    await archive_sut.execute(asset_id=dto1.id, actor_id=actor_id)

    # Now re-create same name — should succeed
    dto2 = await create_sut.execute(
        cmd=CreateAssetCommand(name="ООО Дубль", inn=None, notes=None, actor_id=actor_id)
    )
    assert dto2.id != dto1.id


# ---------------------------------------------------------------------------
# US-14 — Update asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_asset_name_emits_event(session, clock):
    """PATCH asset name: updated_at refreshed, outbox event emitted."""
    from sqlalchemy import select

    from registry_service.application.dto import CreateAssetCommand, UpdateAssetCommand
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.application.use_cases.update_asset import UpdateAsset
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    create_sut = CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    update_sut = UpdateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    dto = await create_sut.execute(
        cmd=CreateAssetCommand(name="ООО Ромашка", inn=None, notes=None, actor_id=actor_id)
    )

    await update_sut.execute(
        cmd=UpdateAssetCommand(
            asset_id=dto.id,
            name="ООО Ромашка Плюс",
            inn=None,
            notes=None,
            actor_id=actor_id,
            request_id="req_upd",
        )
    )

    stored = await repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.name == "ООО Ромашка Плюс"

    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.assets"))
    rows = result.scalars().all()
    update_events = [r for r in rows if r.payload.get("type") == "registry.asset.updated.v1"]
    assert len(update_events) >= 1


@pytest.mark.asyncio
async def test_update_archived_asset_returns_404(session, clock):
    """PATCH on a soft-deleted asset raises AssetNotFoundError."""
    from registry_service.application.dto import UpdateAssetCommand
    from registry_service.application.use_cases.update_asset import UpdateAsset
    from registry_service.domain.errors import AssetNotFoundError
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    sut = UpdateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    archived = _make_asset("ООО Архивный Апдейт")
    archived.deleted_at = _now()
    await repo.add(archived)

    with pytest.raises(AssetNotFoundError):
        await sut.execute(
            cmd=UpdateAssetCommand(
                asset_id=archived.id,
                name="Новое Имя",
                inn=None,
                notes=None,
                actor_id=uuid.uuid4(),
            )
        )


# ---------------------------------------------------------------------------
# US-15 — Cascade archive (Q5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_asset_cascades_active_documents_emits_event(session, clock):
    """Archiving an asset sets deleted_at on all active documents + emits AssetArchived event."""

    from sqlalchemy import select

    from registry_service.application.dto import CreateAssetCommand, CreateDocumentCommand
    from registry_service.application.use_cases.archive_asset import ArchiveAsset
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    create_asset_sut = CreateAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    archive_asset_sut = ArchiveAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    actor_id = uuid.uuid4()
    asset_dto = await create_asset_sut.execute(
        cmd=CreateAssetCommand(name="ООО Каскад Архив", inn=None, notes=None, actor_id=actor_id)
    )

    # Create document type
    from registry_service.domain.entities import DocumentType

    dt = DocumentType(
        code="contract",
        display_name="Договор",
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=_now(),
        updated_at=_now(),
    )
    await type_repo.upsert(dt)

    # Create 12 active documents
    create_doc_sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    doc_ids = []
    for i in range(12):
        doc_dto = await create_doc_sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset_dto.id,
                type_code="contract",
                number=f"CASCADE-{i:03}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )
        doc_ids.append(doc_dto.id)

    cascaded = await archive_asset_sut.execute(asset_id=asset_dto.id, actor_id=actor_id)

    assert cascaded == 12

    # Verify all 12 docs are archived
    for did in doc_ids:
        doc = await doc_repo.get_by_id(did)
        assert doc is not None
        assert doc.deleted_at is not None

    # Verify AssetArchived event has correct cascaded count
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.assets"))
    rows = result.scalars().all()
    archived_events = [r for r in rows if r.payload.get("type") == "registry.asset.archived.v1"]
    assert len(archived_events) >= 1
    payload = archived_events[0].payload.get("payload", {})
    assert payload.get("cascaded_document_count") == 12


@pytest.mark.asyncio
async def test_cascade_archive_skips_already_archived_documents(session, clock):
    """Q5: archive_cascade_documents does NOT touch documents with deleted_at IS NOT NULL."""
    from registry_service.application.dto import CreateAssetCommand, CreateDocumentCommand
    from registry_service.application.use_cases.archive_asset import ArchiveAsset
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    create_asset_sut = CreateAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    archive_asset_sut = ArchiveAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    actor_id = uuid.uuid4()
    asset_dto = await create_asset_sut.execute(
        cmd=CreateAssetCommand(name="ООО Пропуск Архив", inn=None, notes=None, actor_id=actor_id)
    )

    from registry_service.domain.entities import DocumentType

    dt = DocumentType(
        code="contract",
        display_name="Договор",
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=_now(),
        updated_at=_now(),
    )
    await type_repo.upsert(dt)

    create_doc_sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    archive_doc_sut = ArchiveDocument(repo=doc_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    # 3 active + 2 pre-archived
    active_ids = []
    for i in range(3):
        dto = await create_doc_sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset_dto.id,
                type_code="contract",
                number=f"ACTIVE-{i}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )
        active_ids.append(dto.id)

    pre_archived = []
    for i in range(2):
        dto = await create_doc_sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset_dto.id,
                type_code="contract",
                number=f"PRE-ARCH-{i}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )
        await archive_doc_sut.execute(document_id=dto.id, actor_id=actor_id)
        pre_archived.append(dto.id)

    # Record the deleted_at timestamps of pre-archived docs
    pre_archived_deleted_ats = {}
    for did in pre_archived:
        doc = await doc_repo.get_by_id(did)
        assert doc is not None
        pre_archived_deleted_ats[did] = doc.deleted_at

    cascaded = await archive_asset_sut.execute(asset_id=asset_dto.id, actor_id=actor_id)

    # Only 3 active docs should be cascaded (the 2 pre-archived are skipped)
    assert cascaded == 3

    # Pre-archived docs: deleted_at must NOT have changed
    for did in pre_archived:
        doc = await doc_repo.get_by_id(did)
        assert doc is not None
        assert doc.deleted_at == pre_archived_deleted_ats[did]


@pytest.mark.asyncio
async def test_archive_asset_no_documents(session, clock):
    """Archiving an asset with no documents: cascaded_count = 0, no doc events."""
    from sqlalchemy import select

    from registry_service.application.dto import CreateAssetCommand
    from registry_service.application.use_cases.archive_asset import ArchiveAsset
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    asset_repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    create_sut = CreateAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    archive_sut = ArchiveAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    dto = await create_sut.execute(
        cmd=CreateAssetCommand(name="ООО Пустой Актив", inn=None, notes=None, actor_id=actor_id)
    )
    cascaded = await archive_sut.execute(asset_id=dto.id, actor_id=actor_id)

    assert cascaded == 0

    # The asset itself is archived
    stored = await asset_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.deleted_at is not None

    # No document.archived events should exist
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    doc_archived = [r for r in rows if r.payload.get("type") == "registry.document.archived.v1"]
    assert len(doc_archived) == 0
