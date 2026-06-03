# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for CreateDocument use case (US-5)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from registry_service.application.dto import CreateDocumentCommand
from registry_service.application.use_cases.create_document import CreateDocument
from registry_service.domain.entities import Asset, DocumentType
from registry_service.domain.errors import AssetArchivedError, DocumentTypeNotFoundError
from tests.unit.use_cases.fakes import (
    FakeAssetRepository,
    FakeClock,
    FakeDocumentRepository,
    FakeDocumentTypeRepository,
    FakeEventOutbox,
)


@pytest.fixture
def actor_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def asset() -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=uuid.uuid4(),
        name="ООО Ромашка",
        inn=None,
        notes=None,
        status="active",
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


@pytest.fixture
def doc_type() -> DocumentType:
    now = datetime.now(tz=UTC)
    return DocumentType(
        code="contract",
        display_name="Договор",
        pre_notice_days=[30, 7, 1],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def use_case(asset: Asset, doc_type: DocumentType) -> CreateDocument:
    asset_repo = FakeAssetRepository([asset])
    type_repo = FakeDocumentTypeRepository([doc_type])
    doc_repo = FakeDocumentRepository()
    outbox = FakeEventOutbox()
    clock = FakeClock()
    return CreateDocument(  # type: ignore[return-value]
        doc_repo=doc_repo,
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=outbox,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_create_document_happy_path(
    use_case: CreateDocument,
    asset: Asset,
    actor_id: uuid.UUID,
) -> None:
    dto = await use_case.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="ДГ-2026-99",
            issue_date=date(2026, 1, 1),
            expiry_date=date(2027, 1, 1),
            responsible_user_id=None,
            notes="Тестовый договор",
            actor_id=actor_id,
        )
    )
    assert dto.asset_id == asset.id
    assert dto.type_code == "contract"
    assert dto.number == "ДГ-2026-99"
    assert dto.status == "active"
    assert dto.deleted_at is None
    assert dto.urgency_status == "ok"  # 239 days from 2026-05-07 > 30


@pytest.mark.asyncio
async def test_create_document_no_expiry_is_ok_status(
    use_case: CreateDocument,
    asset: Asset,
    actor_id: uuid.UUID,
) -> None:
    dto = await use_case.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number=None,
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    assert dto.expiry_date is None
    assert dto.urgency_status == "ok"


@pytest.mark.asyncio
async def test_create_document_archived_asset_rejected(asset: Asset) -> None:
    from datetime import UTC

    archived = Asset(
        id=asset.id,
        name=asset.name,
        inn=asset.inn,
        notes=asset.notes,
        status="archived",
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        deleted_at=datetime.now(tz=UTC),
    )
    from registry_service.domain.entities import DocumentType as DT

    asset_repo = FakeAssetRepository([archived])
    now = datetime.now(tz=UTC)
    doc_type = DT(
        code="contract",
        display_name="Договор",
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=now,
        updated_at=now,
    )
    type_repo = FakeDocumentTypeRepository([doc_type])
    use_case = CreateDocument(  # type: ignore[misc]
        doc_repo=FakeDocumentRepository(),
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=FakeEventOutbox(),  # type: ignore[arg-type]
        clock=FakeClock(),
    )

    with pytest.raises(AssetArchivedError):
        await use_case.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset.id,
                type_code="contract",
                number=None,
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_create_document_unknown_type_rejected(asset: Asset) -> None:
    asset_repo = FakeAssetRepository([asset])
    type_repo = FakeDocumentTypeRepository()  # empty
    use_case = CreateDocument(  # type: ignore[misc]
        doc_repo=FakeDocumentRepository(),
        asset_repo=asset_repo,
        type_repo=type_repo,
        outbox=FakeEventOutbox(),  # type: ignore[arg-type]
        clock=FakeClock(),
    )

    with pytest.raises(DocumentTypeNotFoundError):
        await use_case.execute(
            cmd=CreateDocumentCommand(
                asset_id=asset.id,
                type_code="nonexistent",
                number=None,
                issue_date=None,
                expiry_date=None,
                responsible_user_id=None,
                notes=None,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_create_document_emits_outbox_event(
    use_case: CreateDocument,
    asset: Asset,
    actor_id: uuid.UUID,
) -> None:
    # Access outbox via the use_case attribute (return value not inspected here)
    await use_case.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="X",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )
    outbox: FakeEventOutbox = use_case.outbox  # type: ignore[assignment]
    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.document.created.v1"
    assert envelope.actor_id == actor_id
    assert topic == "registry.documents"
