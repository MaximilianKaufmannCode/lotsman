# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for DeleteAttachment use case (US-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.use_cases.delete_attachment import DeleteAttachment
from registry_service.domain.entities import Attachment, Document
from registry_service.domain.errors import (
    AttachmentDeleteArchivedDocumentError,
    AttachmentNotFoundError,
)
from tests.unit.use_cases.fakes import (
    FakeAttachmentRepository,
    FakeAttachmentStorage,
    FakeClock,
    FakeDocumentRepository,
    FakeEventOutbox,
)


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


def _make_attachment(doc_id: uuid.UUID) -> Attachment:
    now = datetime.now(tz=UTC)
    return Attachment(
        id=uuid.uuid4(),
        document_id=doc_id,
        original_filename="contract.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        storage_path=f"attachments/test/{uuid.uuid4()}",
        created_by=uuid.uuid4(),
        created_at=now,
    )


@pytest.mark.asyncio
async def test_delete_attachment_happy_path() -> None:
    """Delete attachment from active doc → row removed, file deleted, event emitted."""
    doc = _make_doc()
    attachment = _make_attachment(doc.id)

    attachment_repo = FakeAttachmentRepository([attachment])
    doc_repo = FakeDocumentRepository([doc])
    storage = FakeAttachmentStorage()
    # Pre-populate storage with the attachment's file
    storage.saved[attachment.storage_path] = b"fake pdf content"
    outbox = FakeEventOutbox()

    use_case = DeleteAttachment(
        attachment_repo=attachment_repo,
        doc_repo=doc_repo,
        storage=storage,
        outbox=outbox,
        clock=FakeClock(),
    )  # type: ignore[arg-type]

    await use_case.execute(attachment_id=attachment.id, actor_id=uuid.uuid4())

    # Attachment row gone
    assert await attachment_repo.get_by_id(attachment.id) is None

    # File deleted from storage
    assert attachment.storage_path in storage.deleted

    # Outbox event emitted
    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    # AttachmentDeleted uses registry.document.updated.v1 per events.py
    assert envelope.type == "registry.document.updated.v1"
    assert topic == "registry.documents"


@pytest.mark.asyncio
async def test_delete_nonexistent_attachment_raises() -> None:
    use_case = DeleteAttachment(
        attachment_repo=FakeAttachmentRepository(),
        doc_repo=FakeDocumentRepository(),
        storage=FakeAttachmentStorage(),
        outbox=FakeEventOutbox(),
        clock=FakeClock(),
    )  # type: ignore[arg-type]

    with pytest.raises(AttachmentNotFoundError):
        await use_case.execute(attachment_id=uuid.uuid4(), actor_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_delete_from_archived_doc_raises() -> None:
    """Cannot delete attachment from an archived document (US-11 AC)."""
    doc = _make_doc(deleted_at=datetime.now(tz=UTC))
    attachment = _make_attachment(doc.id)

    use_case = DeleteAttachment(
        attachment_repo=FakeAttachmentRepository([attachment]),
        doc_repo=FakeDocumentRepository([doc]),
        storage=FakeAttachmentStorage(),
        outbox=FakeEventOutbox(),
        clock=FakeClock(),
    )  # type: ignore[arg-type]

    with pytest.raises(AttachmentDeleteArchivedDocumentError):
        await use_case.execute(attachment_id=attachment.id, actor_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_delete_is_not_idempotent() -> None:
    """Second call to delete same attachment raises AttachmentNotFoundError."""
    doc = _make_doc()
    attachment = _make_attachment(doc.id)
    attachment_repo = FakeAttachmentRepository([attachment])

    use_case = DeleteAttachment(
        attachment_repo=attachment_repo,
        doc_repo=FakeDocumentRepository([doc]),
        storage=FakeAttachmentStorage(),
        outbox=FakeEventOutbox(),
        clock=FakeClock(),
    )  # type: ignore[arg-type]

    # First delete succeeds
    await use_case.execute(attachment_id=attachment.id, actor_id=uuid.uuid4())

    # Second delete must raise
    with pytest.raises(AttachmentNotFoundError):
        await use_case.execute(attachment_id=attachment.id, actor_id=uuid.uuid4())
