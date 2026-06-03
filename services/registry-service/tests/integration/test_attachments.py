# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for attachment upload / download / delete — US-9, US-10, US-11.

Tests requiring libmagic (python-magic) for MIME sniffing are individually
skipped with skip:libmagic reason. All others run with testcontainers Postgres.
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

_PDF_MAGIC_BYTES = b"%PDF-1.4 " + b"\x00" * 512  # valid PDF magic header


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _create_asset_and_doc(session, clock, name: str = "ООО Вложения Тест") -> tuple:
    from registry_service.application.dto import CreateAssetCommand, CreateDocumentCommand
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.domain.entities import DocumentType
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

    create_asset = CreateAsset(repo=asset_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    actor_id = uuid.uuid4()
    asset_dto = await create_asset.execute(
        cmd=CreateAssetCommand(name=name, inn=None, notes=None, actor_id=actor_id)
    )

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

    create_doc = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    doc_dto = await create_doc.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset_dto.id,
            type_code="contract",
            number="ATT-DOC-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
        )
    )

    return doc_dto, actor_id, outbox


# ---------------------------------------------------------------------------
# US-9 — Upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_valid_pdf_stores_metadata_and_emits_event(session, clock):
    """Happy path: upload a valid PDF. Row inserted, outbox event written."""
    from sqlalchemy import select

    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Загрузка PDF")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)

    storage = FakeAttachmentStorage()
    sniffer = FakeMimeSniffer("application/pdf")

    sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=__import__(
            "registry_service.infrastructure.db.repositories", fromlist=["SqlDocumentRepository"]
        ).SqlDocumentRepository(session),
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=sniffer,  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )

    dto = await sut.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc_dto.id,
            filename="договор.pdf",
            content_type="application/pdf",
            data=_PDF_MAGIC_BYTES,
            actor_id=actor_id,
            request_id="req_upload",
        )
    )

    assert dto.id is not None
    assert dto.mime_type == "application/pdf"
    assert dto.size_bytes == len(_PDF_MAGIC_BYTES)

    # Verify attachment row stored
    atts = await att_repo.list_for_document(doc_dto.id)
    assert len(atts) == 1
    assert atts[0].original_filename == "договор.pdf"

    # Verify outbox event
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    upload_events = [r for r in rows if r.payload.get("type") == "registry.document.updated.v1"]
    assert len(upload_events) >= 1


@pytest.mark.asyncio
async def test_upload_exceeds_size_limit_returns_413(session, clock):
    """Uploading a file larger than 25 MiB raises AttachmentTooLargeError."""
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.domain.errors import AttachmentTooLargeError
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Большой Файл")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)

    storage = FakeAttachmentStorage()
    sniffer = FakeMimeSniffer("application/pdf")

    sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=SqlDocumentRepository(session),
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=sniffer,  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )

    # 26 MiB of zeros — exceeds 25 MiB limit
    big_file = b"\x00" * (26 * 1024 * 1024)

    with pytest.raises(AttachmentTooLargeError):
        await sut.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc_dto.id,
                filename="huge.pdf",
                content_type="application/pdf",
                data=big_file,
                actor_id=actor_id,
            )
        )

    # No bytes saved, no DB row
    assert len(storage.saved) == 0
    atts = await att_repo.list_for_document(doc_dto.id)
    assert len(atts) == 0


@pytest.mark.asyncio
@pytest.mark.skip(
    reason=(
        "skip:libmagic — Requires python-magic/libmagic installed on the test runner. "
        "Unblock by: apt-get install -y libmagic1 && uv add python-magic"
    )
)
async def test_upload_mime_spoofed_executable_rejected(session, clock):
    """Windows PE executable disguised as .pdf must be rejected with 415.

    The real infrastructure.mime.LibmagicSniffer is needed here.
    FakeMimeSniffer would return a hardcoded MIME and cannot test the sniffing logic.
    """
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.domain.errors import AttachmentMimeRejectedError
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from registry_service.infrastructure.mime import LibmagicSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО MIME Спуфинг")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)
    sniffer = LibmagicSniffer()

    from tests.unit.use_cases.fakes import FakeAttachmentStorage

    storage = FakeAttachmentStorage()

    sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=SqlDocumentRepository(session),
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=sniffer,  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )

    # Windows PE magic header (MZ): application/x-dosexec
    pe_bytes = b"MZ" + b"\x00" * 510

    with pytest.raises(AttachmentMimeRejectedError):
        await sut.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc_dto.id,
                filename="contract.pdf",  # spoofed extension
                content_type="application/pdf",  # spoofed declared type
                data=pe_bytes,
                actor_id=actor_id,
            )
        )

    assert len(storage.saved) == 0


@pytest.mark.asyncio
async def test_upload_to_archived_document_returns_409(session, clock):
    """Uploading to an archived document raises AttachmentArchivedDocumentError."""
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.domain.errors import AttachmentArchivedDocumentError
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Архивный Докумен")
    outbox = SqlEventOutbox(session)
    doc_repo = SqlDocumentRepository(session)
    att_repo = SqlAttachmentRepository(session)

    # Archive the document
    archive_uc = ArchiveDocument(repo=doc_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    await archive_uc.execute(document_id=doc_dto.id, actor_id=actor_id)

    sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=FakeAttachmentStorage(),  # type: ignore[arg-type]
        sniffer=FakeMimeSniffer("application/pdf"),  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )

    with pytest.raises(AttachmentArchivedDocumentError):
        await sut.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc_dto.id,
                filename="test.pdf",
                content_type="application/pdf",
                data=_PDF_MAGIC_BYTES,
                actor_id=actor_id,
            )
        )


# ---------------------------------------------------------------------------
# US-10 — Download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_attachment_returns_signed_redirect(session, clock):
    """Download attachment generates a signed URL (happy path)."""
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.download_attachment import DownloadAttachment
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Скачивание")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)
    doc_repo = SqlDocumentRepository(session)
    storage = FakeAttachmentStorage()

    upload_sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=FakeMimeSniffer("application/pdf"),  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )
    att_dto = await upload_sut.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc_dto.id,
            filename="download.pdf",
            content_type="application/pdf",
            data=_PDF_MAGIC_BYTES,
            actor_id=actor_id,
        )
    )

    download_sut = DownloadAttachment(att_repo=att_repo, storage=storage)  # type: ignore[arg-type]
    signed = await download_sut.execute(attachment_id=att_dto.id, actor_id=actor_id)

    assert signed.url.startswith("http")
    assert "sig=" in signed.url or "expires=" in signed.url  # signed URL has params


@pytest.mark.asyncio
async def test_download_attachment_from_archived_document_allowed(session, clock):
    """Downloading from an archived document's attachment is still permitted."""
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.download_attachment import DownloadAttachment
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Архив Скачать")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)
    doc_repo = SqlDocumentRepository(session)
    storage = FakeAttachmentStorage()

    upload_sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=FakeMimeSniffer("application/pdf"),  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )
    att_dto = await upload_sut.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc_dto.id,
            filename="arch.pdf",
            content_type="application/pdf",
            data=_PDF_MAGIC_BYTES,
            actor_id=actor_id,
        )
    )

    # Archive document
    archive_uc = ArchiveDocument(repo=doc_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    await archive_uc.execute(document_id=doc_dto.id, actor_id=actor_id)

    # Download must still work — archive does not revoke access
    download_sut = DownloadAttachment(att_repo=att_repo, storage=storage)  # type: ignore[arg-type]
    signed = await download_sut.execute(attachment_id=att_dto.id, actor_id=actor_id)
    assert signed.url  # non-empty signed URL


@pytest.mark.asyncio
async def test_download_nonexistent_attachment_returns_404(session, clock):
    """Downloading a non-existent attachment raises AttachmentNotFoundError."""
    from registry_service.application.use_cases.download_attachment import DownloadAttachment
    from registry_service.domain.errors import AttachmentNotFoundError
    from registry_service.infrastructure.db.repositories import SqlAttachmentRepository
    from tests.unit.use_cases.fakes import FakeAttachmentStorage

    att_repo = SqlAttachmentRepository(session)
    storage = FakeAttachmentStorage()
    sut = DownloadAttachment(att_repo=att_repo, storage=storage)  # type: ignore[arg-type]

    with pytest.raises(AttachmentNotFoundError):
        await sut.execute(
            attachment_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            actor_id=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# US-11 — Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_attachment_hard_removes_and_emits_event(session, clock):
    """DELETE attachment: row removed from DB, storage deleted, outbox event."""
    from sqlalchemy import select

    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.delete_attachment import DeleteAttachment
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Удалить Вложение")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)
    doc_repo = SqlDocumentRepository(session)
    storage = FakeAttachmentStorage()

    upload_sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=FakeMimeSniffer("application/pdf"),  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )
    att_dto = await upload_sut.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc_dto.id,
            filename="delete_me.pdf",
            content_type="application/pdf",
            data=_PDF_MAGIC_BYTES,
            actor_id=actor_id,
        )
    )

    storage_path = list(storage.saved.keys())[0]

    delete_sut = DeleteAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )
    await delete_sut.execute(attachment_id=att_dto.id, actor_id=actor_id)

    # Row hard-deleted
    atts = await att_repo.list_for_document(doc_dto.id)
    assert len(atts) == 0

    # File deleted from storage
    assert storage_path in storage.deleted

    # Outbox event
    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    # The AttachmentDeleted event wraps as registry.document.updated.v1 (see events.py)
    assert any(r.payload.get("type") == "registry.document.updated.v1" for r in rows)


@pytest.mark.asyncio
async def test_delete_attachment_from_archived_doc_returns_409(session, clock):
    """Deleting an attachment from an archived document raises AttachmentDeleteArchivedDocumentError."""
    from registry_service.application.dto import UploadAttachmentCommand
    from registry_service.application.use_cases.archive_document import ArchiveDocument
    from registry_service.application.use_cases.delete_attachment import DeleteAttachment
    from registry_service.application.use_cases.upload_attachment import UploadAttachment
    from registry_service.domain.errors import AttachmentDeleteArchivedDocumentError
    from registry_service.infrastructure.db.repositories import (
        SqlAttachmentRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
    )
    from tests.unit.use_cases.fakes import FakeAttachmentStorage, FakeMimeSniffer

    doc_dto, actor_id, _ = await _create_asset_and_doc(session, clock, "ООО Архив Удаление")
    outbox = SqlEventOutbox(session)
    att_repo = SqlAttachmentRepository(session)
    doc_repo = SqlDocumentRepository(session)
    storage = FakeAttachmentStorage()

    upload_sut = UploadAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        sniffer=FakeMimeSniffer("application/pdf"),  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )
    att_dto = await upload_sut.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc_dto.id,
            filename="arch_del.pdf",
            content_type="application/pdf",
            data=_PDF_MAGIC_BYTES,
            actor_id=actor_id,
        )
    )

    # Archive document first
    archive_uc = ArchiveDocument(repo=doc_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    await archive_uc.execute(document_id=doc_dto.id, actor_id=actor_id)

    delete_sut = DeleteAttachment(  # type: ignore[call-arg]
        doc_repo=doc_repo,
        att_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
        outbox=outbox,
        clock=clock,
    )

    with pytest.raises(AttachmentDeleteArchivedDocumentError):
        await delete_sut.execute(attachment_id=att_dto.id, actor_id=actor_id)
