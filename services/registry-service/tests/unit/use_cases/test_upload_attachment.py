# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for UploadAttachment use case (US-9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import UploadAttachmentCommand
from registry_service.application.use_cases.upload_attachment import UploadAttachment
from registry_service.domain.entities import Document
from registry_service.domain.errors import (
    AttachmentArchivedDocumentError,
    AttachmentMimeRejectedError,
    AttachmentTooLargeError,
    DocumentNotFoundError,
)
from tests.unit.use_cases.fakes import (
    FakeAttachmentRepository,
    FakeAttachmentStorage,
    FakeClock,
    FakeDocumentRepository,
    FakeEventOutbox,
    FakeMimeSniffer,
)

_PDF_BYTES = b"%PDF-1.4 fake content"


def _make_doc(*, deleted_at: datetime | None = None) -> Document:
    now = datetime.now(tz=UTC)
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        type_code="contract",
        number="TEST-001",
        issue_date=None,
        expiry_date=None,
        responsible_user_id=None,
        status="archived" if deleted_at else "active",
        notes=None,
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )


def _make_use_case(
    doc: Document | None = None,
    *,
    mime: str = "application/pdf",
) -> UploadAttachment:
    doc_repo = FakeDocumentRepository([doc] if doc else [])
    return UploadAttachment(
        doc_repo=doc_repo,
        attachment_repo=FakeAttachmentRepository(),
        storage=FakeAttachmentStorage(),
        mime_sniffer=FakeMimeSniffer(mime),
        outbox=FakeEventOutbox(),
        clock=FakeClock(),
    )  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_upload_happy_path() -> None:
    """Happy path: PDF to active doc → attachment row created, outbox event emitted."""
    doc = _make_doc()
    use_case = _make_use_case(doc)

    dto = await use_case.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc.id,
            filename="contract.pdf",
            content_type="application/pdf",
            data=_PDF_BYTES,
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.document_id == doc.id
    assert dto.original_filename == "contract.pdf"
    assert dto.mime_type == "application/pdf"
    assert dto.size_bytes == len(_PDF_BYTES)
    assert len(dto.sha256) == 64  # hex SHA-256

    outbox: FakeEventOutbox = use_case.outbox  # type: ignore[assignment]
    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    # AttachmentUploaded uses registry.document.updated.v1 per events.py
    assert envelope.type == "registry.document.updated.v1"
    assert topic == "registry.documents"


@pytest.mark.asyncio
async def test_upload_to_nonexistent_doc_raises() -> None:
    use_case = _make_use_case()  # empty repo

    with pytest.raises(DocumentNotFoundError):
        await use_case.execute(
            cmd=UploadAttachmentCommand(
                document_id=uuid.uuid4(),
                filename="x.pdf",
                content_type="application/pdf",
                data=_PDF_BYTES,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_upload_to_archived_doc_raises() -> None:
    """Uploading to a soft-deleted document must raise AttachmentArchivedDocumentError."""
    doc = _make_doc(deleted_at=datetime.now(tz=UTC))
    use_case = _make_use_case(doc)

    with pytest.raises(AttachmentArchivedDocumentError):
        await use_case.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc.id,
                filename="x.pdf",
                content_type="application/pdf",
                data=_PDF_BYTES,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_upload_exceeds_size_limit_raises() -> None:
    """Files over 25 MiB must be rejected with AttachmentTooLargeError (Q1)."""
    doc = _make_doc()
    use_case = _make_use_case(doc)

    oversized = b"x" * (25 * 1024 * 1024 + 1)

    with pytest.raises(AttachmentTooLargeError):
        await use_case.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc.id,
                filename="big.pdf",
                content_type="application/pdf",
                data=oversized,
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_upload_rejected_mime_from_sniff_raises() -> None:
    """Sniffed MIME not in allowlist must raise AttachmentMimeRejectedError (Q7, MIME spoofing)."""
    doc = _make_doc()
    # Client claims PDF but bytes sniff as an executable
    use_case = _make_use_case(doc, mime="application/x-dosexec")

    with pytest.raises(AttachmentMimeRejectedError):
        await use_case.execute(
            cmd=UploadAttachmentCommand(
                document_id=doc.id,
                filename="malicious.pdf",  # disguised executable
                content_type="application/pdf",
                data=b"MZ\x00\x00fake exe",
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_upload_stores_sniffed_mime_not_client_declared() -> None:
    """The stored mime_type must come from the sniffer, not the client Content-Type."""
    doc = _make_doc()
    # Client declares jpeg but sniffer correctly identifies it as png
    use_case = _make_use_case(doc, mime="image/png")

    dto = await use_case.execute(
        cmd=UploadAttachmentCommand(
            document_id=doc.id,
            filename="image.png",
            content_type="image/jpeg",  # wrong client declaration
            data=b"\x89PNG\r\n\x1a\n",
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.mime_type == "image/png"  # sniffer wins
