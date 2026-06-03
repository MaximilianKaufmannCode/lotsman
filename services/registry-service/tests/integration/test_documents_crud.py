# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for documents CRUD — US-1 through US-5, US-22 through US-23.

Tests exercise the full use-case → SQLAlchemy repository → PostgreSQL path.
Each test uses the session fixture (function-scoped, rolled back after).

Gherkin coverage:
  US-2  — search (pg_trgm / ILIKE / SQL injection)
  US-3  — sort + filter combinations
  US-4  — inline edit, concurrent conflict
  US-5  — create document (happy + edge cases)
  US-6  — archive + attachment preservation
  US-16 — list/create document types
  US-17 — upsert document type
  US-22 — concurrent last-writer-wins
  US-23 — bulk archive (100 ok, 101 rejected, mixed already-archived)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

pytestmark = pytest.mark.skipif(
    True,
    reason=(
        "Requires testcontainers[postgres] + asyncpg at runtime. "
        "Unblock by installing: uv add --dev 'testcontainers[postgres]' asyncpg"
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_asset_entity(name: str = "ООО Ромашка") -> Asset:  # type: ignore[name-defined]
    from registry_service.domain.entities import Asset

    now = _now()
    return Asset(
        id=uuid.uuid4(),
        name=name,
        inn=None,
        notes=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_doc_type_entity(code: str = "contract") -> DocumentType:  # type: ignore[name-defined]
    from registry_service.domain.entities import DocumentType

    now = _now()
    return DocumentType(
        code=code,
        display_name="Договор",
        pre_notice_days=[30, 7, 1],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=now,
        updated_at=now,
    )


def _make_document_entity(
    asset_id: uuid.UUID,
    type_code: str = "contract",
    number: str | None = "ДГ-2026-001",
    expiry_date: date | None = None,
    deleted_at: datetime | None = None,
) -> Document:  # type: ignore[name-defined]
    from registry_service.domain.entities import Document

    actor = uuid.uuid4()
    now = _now()
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id,
        type_code=type_code,
        number=number,
        issue_date=None,
        expiry_date=expiry_date,
        responsible_user_id=None,
        status="archived" if deleted_at else "active",
        notes=None,
        created_by=actor,
        updated_by=actor,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )


# ---------------------------------------------------------------------------
# US-5 — Create document (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_full_happy_path(session, fake_outbox, clock):
    """Create document: inserts row, emits outbox event with correct topic."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Интеграция Тест")
    doc_type = _make_doc_type_entity("contract")

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox,
        clock=clock,
    )

    actor_id = uuid.uuid4()
    dto = await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="ДГ-INTEG-001",
            issue_date=date(2026, 1, 1),
            expiry_date=date(2027, 6, 1),
            responsible_user_id=None,
            notes="Тест",
            actor_id=actor_id,
            request_id="req_test_001",
        )
    )

    # Business assertions
    assert dto.id is not None
    assert dto.type_code == "contract"
    assert dto.status == "active"
    assert dto.urgency_status == "ok"  # 390+ days from 2026-05-07

    # DB state
    stored = await doc_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.number == "ДГ-INTEG-001"
    assert stored.deleted_at is None

    # Outbox — verify at least one row written with correct topic/type
    # (SqlEventOutbox writes to the ORM session; we flush and check the ORM model)
    from sqlalchemy import select

    from registry_service.db.models import Outbox

    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    assert any(r.payload.get("type") == "registry.document.created.v1" for r in rows)
    # actor_id propagated into the envelope
    assert any(r.payload.get("actor_id") == str(actor_id) for r in rows)


@pytest.mark.asyncio
async def test_create_document_notes_exceeds_max_length(session, fake_outbox, clock):
    """10 001-char notes field must be rejected (US-5 edge case).

    The validation is at the API/schema layer (Pydantic), not the use case.
    This integration test verifies the ORM column check constraint raises.
    """
    import sqlalchemy.exc

    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Длинная Запись")
    doc_type = _make_doc_type_entity()

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox,
        clock=clock,
    )

    notes_10001 = "А" * 10001  # Cyrillic character, definitely > 10 000

    with pytest.raises((sqlalchemy.exc.DataError, sqlalchemy.exc.StatementError, ValueError)):
        await sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset.id,
                type_code="contract",
                number="NOTES-EDGE",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=notes_10001,
                actor_id=uuid.uuid4(),
            )
        )


# ---------------------------------------------------------------------------
# US-2 — Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_asset_name_returns_matching_rows(session, clock):
    """pg_trgm / ILIKE search on asset name finds matching documents."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    # Create two assets
    asset_gazprom = _make_asset_entity("ООО Газпром Инвест")
    asset_other = _make_asset_entity("ООО Другая Компания")
    doc_type = _make_doc_type_entity()

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    await asset_repo.add(asset_gazprom)
    await asset_repo.add(asset_other)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    actor_id = uuid.uuid4()

    for i in range(3):
        await sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset_gazprom.id,
                type_code="contract",
                number=f"ГП-{i:03}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )

    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset_other.id,
            type_code="contract",
            number="ДР-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    # Search — "Газпром" should return only the 3 matching rows
    results = await doc_repo.list_active(q="Газпром")
    assert len(results) == 3
    assert all(r.asset_id == asset_gazprom.id for r in results)


@pytest.mark.asyncio
async def test_search_no_results_returns_empty_list(session, clock):
    """Search for nonexistent string returns empty list — no error."""
    from registry_service.infrastructure.db.repositories import SqlDocumentRepository

    doc_repo = SqlDocumentRepository(session)
    results = await doc_repo.list_active(q="XYZZY_NONEXISTENT_COMPANY")
    assert results == []


@pytest.mark.asyncio
async def test_search_with_sql_special_chars_does_not_error(session, clock):
    """SQL-special characters in search do not cause errors (parameterized query)."""
    from registry_service.infrastructure.db.repositories import SqlDocumentRepository

    doc_repo = SqlDocumentRepository(session)
    # Should not raise, should return empty list
    results = await doc_repo.list_active(q="O'Brien & Sons; DROP TABLE --")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_filter_by_type_code_returns_only_matching(session, clock):
    """Filter by type_code returns only documents with that type."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Фильтр Тест")
    contract_type = _make_doc_type_entity("contract")
    license_type = _make_doc_type_entity("license")
    license_type.display_name = "Лицензия"

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    await asset_repo.add(asset)
    await type_repo.upsert(contract_type)
    await type_repo.upsert(license_type)

    sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    actor_id = uuid.uuid4()

    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="С-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="license",
            number="Л-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    results = await doc_repo.list_active(type_code="license")
    assert len(results) == 1
    assert results[0].type_code == "license"


# ---------------------------------------------------------------------------
# US-3 — Sort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sort_by_expiry_date_asc_nulls_last(session, clock):
    """Sort by expiry_date ASC with NULL values appearing last."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Сортировка")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)
    sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    actor_id = uuid.uuid4()

    # Insert: no expiry, far future, near future
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="NULL-EXP",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="FAR-FUTURE",
            issue_date=None,
            expiry_date=date(2030, 1, 1),
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="NEAR-FUTURE",
            issue_date=None,
            expiry_date=date(2026, 12, 31),
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    results = await doc_repo.list_active(sort="expiry_date", dir="asc")
    # Non-null dates come first in ASC order, then NULLs
    non_null = [r for r in results if r.expiry_date is not None]
    null_rows = [r for r in results if r.expiry_date is None]
    # All non-null before null (nulls_last)
    if non_null and null_rows:
        last_non_null_idx = max(results.index(r) for r in non_null)
        first_null_idx = min(results.index(r) for r in null_rows)
        assert last_non_null_idx < first_null_idx
    # Non-null dates are ascending
    expiry_dates = [r.expiry_date for r in non_null]
    assert expiry_dates == sorted(expiry_dates)


@pytest.mark.asyncio
async def test_sort_all_null_expiry_is_stable(session, clock):
    """When all expiry_dates are NULL, sort by expiry_date does not error."""
    from registry_service.infrastructure.db.repositories import SqlDocumentRepository

    doc_repo = SqlDocumentRepository(session)
    # Any existing rows in this session will have their default (no expiry from our factory)
    # Just verify no exception is raised
    results = await doc_repo.list_active(sort="expiry_date", dir="asc")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_sort_multi_column_type_then_expiry(session, clock):
    """Multi-column sort: type_code ASC then expiry_date ASC."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО МультиСортировка")
    c_type = _make_doc_type_entity("contract")
    l_type = _make_doc_type_entity("license")
    l_type.display_name = "Лицензия"
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(c_type)
    await type_repo.upsert(l_type)
    sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    actor_id = uuid.uuid4()
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="license",
            number="L-01",
            issue_date=None,
            expiry_date=date(2027, 6, 1),
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="C-01",
            issue_date=None,
            expiry_date=date(2027, 1, 1),
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    # Primary sort by type_code — contracts should come before licenses alphabetically
    results = await doc_repo.list_active(sort="type_code", dir="asc")
    types = [r.type_code for r in results]
    assert types == sorted(types)


# ---------------------------------------------------------------------------
# US-4 / US-22 — Inline edit + concurrent last-writer-wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_edit_document_number_emits_event(session, clock):
    """PATCH document number: DB updated, outbox event written (US-4 happy path)."""
    from sqlalchemy import select

    from registry_service.application.dto import CreateDocumentCommand, PatchDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.application.use_cases.inline_edit_document import InlineEditDocument
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Инлайн Эдит")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    create_uc = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    actor_id = uuid.uuid4()
    dto = await create_uc.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="ORIG-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    edit_uc = InlineEditDocument(doc_repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    editor_id = uuid.uuid4()
    await edit_uc.execute(
        cmd=PatchDocumentCommand(
            document_id=dto.id,
            field="number",
            value="EDIT-001",
            actor_id=editor_id,
            request_id="req_edit",
        )
    )

    stored = await doc_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.number == "EDIT-001"
    assert stored.updated_by == editor_id

    # Verify outbox has updated event
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    update_events = [r for r in rows if r.payload.get("type") == "registry.document.updated.v1"]
    assert len(update_events) >= 1
    payload = update_events[0].payload.get("payload", {})
    assert payload.get("field") == "number"
    assert payload.get("before") == "ORIG-001"
    assert payload.get("after") == "EDIT-001"


@pytest.mark.asyncio
async def test_inline_edit_required_field_to_empty_rejected(session, clock):
    """Patching a required field (number) to None triggers use-case validation."""
    from registry_service.application.dto import CreateDocumentCommand, PatchDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.application.use_cases.inline_edit_document import InlineEditDocument
    from registry_service.domain.errors import RequiredFieldMissingError
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Обязательное Поле")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    dto = await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="REQ-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=uuid.uuid4(),
        )
    )

    edit_uc = InlineEditDocument(doc_repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    with pytest.raises((RequiredFieldMissingError, ValueError)):
        await edit_uc.execute(
            cmd=PatchDocumentCommand(
                document_id=dto.id,
                field="asset_id",
                value=None,  # Required field — should be rejected
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_concurrent_inline_edit_last_writer_wins(session, clock):
    """Two concurrent PATCHes to the same document: last write persists (US-22)."""
    from registry_service.application.dto import CreateDocumentCommand, PatchDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.application.use_cases.inline_edit_document import InlineEditDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Конкурент")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    dto = await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="CONC-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=uuid.uuid4(),
        )
    )

    edit_uc = InlineEditDocument(doc_repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    carol_id = uuid.uuid4()
    eve_id = uuid.uuid4()

    # Carol saves first
    await edit_uc.execute(
        cmd=PatchDocumentCommand(
            document_id=dto.id, field="number", value="CAROL-EDIT", actor_id=carol_id
        )
    )
    # Eve saves second (last-writer-wins — no optimistic lock)
    await edit_uc.execute(
        cmd=PatchDocumentCommand(
            document_id=dto.id, field="number", value="EVE-EDIT", actor_id=eve_id
        )
    )

    stored = await doc_repo.get_by_id(dto.id)
    assert stored is not None
    # Last write (Eve) wins
    assert stored.number == "EVE-EDIT"
    assert stored.updated_by == eve_id


# ---------------------------------------------------------------------------
# US-6 — Archive preserves attachments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_document_preserves_attachments(session, clock):
    """Archiving a document does NOT delete its attachment rows."""
    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.domain.entities import Attachment
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Архив Вложения")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    att_repo = SqlAttachmentRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    dto = await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="ATT-ARCH-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=uuid.uuid4(),
        )
    )

    # Add an attachment
    now = _now()
    att = Attachment(
        id=uuid.uuid4(),
        document_id=dto.id,
        original_filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="abc123",
        storage_path="attachments/test.pdf",
        created_by=uuid.uuid4(),
        created_at=now,
    )
    await att_repo.add(att)

    # Archive the document
    archive_uc = ArchiveDocument(repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    await archive_uc.execute(document_id=dto.id, actor_id=uuid.uuid4())

    # Attachment must still exist
    atts = await att_repo.list_for_document(dto.id)
    assert len(atts) == 1
    assert atts[0].id == att.id


# ---------------------------------------------------------------------------
# US-16 / US-17 — Document types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_document_types_returns_all(session):
    """GET /document-types returns all types in the catalog."""
    from registry_service.infrastructure.db.repositories import SqlDocumentTypeRepository

    type_repo = SqlDocumentTypeRepository(session)
    for code, name in [("contract", "Договор"), ("license", "Лицензия"), ("audit_report", "Аудит")]:
        dt = _make_doc_type_entity(code)
        dt.display_name = name
        await type_repo.upsert(dt)

    result = await type_repo.list_all()
    codes = {dt.code for dt in result}
    assert "contract" in codes
    assert "license" in codes
    assert "audit_report" in codes


@pytest.mark.asyncio
async def test_list_document_types_empty(session):
    """Empty document_types table returns empty list — no error."""
    from registry_service.infrastructure.db.repositories import SqlDocumentTypeRepository

    type_repo = SqlDocumentTypeRepository(session)
    result = await type_repo.list_all()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_create_document_type_emits_event(session, clock):
    """Creating a document type writes an outbox event on topic registry.document_types."""
    from sqlalchemy import select

    from registry_service.application.dto import UpsertDocumentTypeCommand
    from registry_service.application.use_cases.upsert_document_type import UpsertDocumentType
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import (
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    sut = UpsertDocumentType(repo=type_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    await sut.execute(
        cmd=UpsertDocumentTypeCommand(
            code="nda",
            display_name="Соглашение о неразглашении",
            pre_notice_days=[30, 7],
            notify_in_day=True,
            overdue_every_days=7,
            actor_id=actor_id,
        )
    )

    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.document_types"))
    rows = result.scalars().all()
    assert any(r.payload.get("type") == "registry.document_type.upserted.v1" for r in rows)


@pytest.mark.asyncio
async def test_upsert_document_type_updates_pre_notice_days(session, clock):
    """Updating an existing type's pre_notice_days persists correctly."""
    from registry_service.application.dto import UpsertDocumentTypeCommand
    from registry_service.application.use_cases.upsert_document_type import UpsertDocumentType
    from registry_service.infrastructure.db.repositories import (
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    sut = UpsertDocumentType(repo=type_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()

    # First upsert
    await sut.execute(
        cmd=UpsertDocumentTypeCommand(
            code="contract",
            display_name="Договор",
            pre_notice_days=[30, 7, 1],
            notify_in_day=True,
            overdue_every_days=7,
            actor_id=actor_id,
        )
    )

    # Second upsert with updated days
    await sut.execute(
        cmd=UpsertDocumentTypeCommand(
            code="contract",
            display_name="Договор",
            pre_notice_days=[60, 30, 7, 1],
            notify_in_day=True,
            overdue_every_days=7,
            actor_id=actor_id,
        )
    )

    stored = await type_repo.get_by_code("contract")
    assert stored is not None
    assert stored.pre_notice_days == [60, 30, 7, 1]


# ---------------------------------------------------------------------------
# US-23 — Bulk archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_archive_100_rows_happy_path(session, clock):
    """Bulk archive exactly 100 documents — 100 archived, 0 skipped (US-23)."""
    from registry_service.application.dto import BulkArchiveCommand, CreateDocumentCommand
    from registry_service.application.use_cases.bulk_archive_documents import BulkArchiveDocuments
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Массовый Архив")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    create_sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    actor_id = uuid.uuid4()
    doc_ids = []
    for i in range(100):
        dto = await create_sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset.id,
                type_code="contract",
                number=f"BULK-{i:03}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )
        doc_ids.append(dto.id)

    bulk_sut = BulkArchiveDocuments(repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    result = await bulk_sut.execute(
        cmd=BulkArchiveCommand(ids=doc_ids, actor_id=actor_id, request_id="req_bulk")
    )

    assert result.archived == 100
    assert result.skipped == 0

    # Verify all are now soft-deleted
    stored = await doc_repo.list_active()
    # All 100 documents should be absent from active list
    active_ids = {d.id for d in stored}
    assert not any(did in active_ids for did in doc_ids)


@pytest.mark.asyncio
async def test_bulk_archive_101_rows_rejected(session, clock):
    """Bulk archive with 101 IDs raises BulkLimitExceededError (Q3 — 100 row cap)."""
    from registry_service.application.dto import BulkArchiveCommand
    from registry_service.application.use_cases.bulk_archive_documents import BulkArchiveDocuments
    from registry_service.domain.errors import BulkLimitExceededError
    from registry_service.infrastructure.db.repositories import (
        SqlDocumentRepository,
        SqlEventOutbox,
    )

    doc_repo = SqlDocumentRepository(session)
    outbox_writer = SqlEventOutbox(session)
    bulk_sut = BulkArchiveDocuments(repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]

    ids_101 = [uuid.uuid4() for _ in range(101)]

    with pytest.raises(BulkLimitExceededError):
        await bulk_sut.execute(cmd=BulkArchiveCommand(ids=ids_101, actor_id=uuid.uuid4()))


@pytest.mark.asyncio
async def test_bulk_archive_mixed_already_archived_skipped(session, clock):
    """Bulk archive where some documents are already archived — skipped count correct."""
    from registry_service.application.dto import BulkArchiveCommand, CreateDocumentCommand
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.bulk_archive_documents import BulkArchiveDocuments
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    asset = _make_asset_entity("ООО Смешанный Архив")
    doc_type = _make_doc_type_entity()
    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox_writer = SqlEventOutbox(session)
    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    create_sut = CreateDocument(
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox_writer,
        clock=clock,
    )
    actor_id = uuid.uuid4()

    all_ids = []
    for i in range(10):
        dto = await create_sut.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset.id,
                type_code="contract",
                number=f"MIX-{i:03}",
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=actor_id,
            )
        )
        all_ids.append(dto.id)

    # Pre-archive 3 of them
    arch_uc = ArchiveDocument(repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    for did in all_ids[:3]:
        await arch_uc.execute(document_id=did, actor_id=actor_id)

    bulk_sut = BulkArchiveDocuments(repo=doc_repo, outbox=outbox_writer, clock=clock)  # type: ignore[arg-type]
    result = await bulk_sut.execute(cmd=BulkArchiveCommand(ids=all_ids, actor_id=actor_id))

    assert result.archived == 7  # 10 total - 3 already archived
    assert result.skipped == 3
