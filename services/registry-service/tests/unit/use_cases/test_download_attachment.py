# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for DownloadAttachment use case (US-10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.use_cases.download_attachment import DownloadAttachment
from registry_service.domain.entities import Attachment
from registry_service.domain.errors import AttachmentNotFoundError
from tests.unit.use_cases.fakes import FakeAttachmentRepository, FakeAttachmentStorage


def _make_attachment(doc_id: uuid.UUID | None = None) -> Attachment:
    now = datetime.now(tz=UTC)
    return Attachment(
        id=uuid.uuid4(),
        document_id=doc_id or uuid.uuid4(),
        original_filename="report.pdf",
        mime_type="application/pdf",
        size_bytes=4096,
        sha256="b" * 64,
        storage_path="attachments/2026/05/test-file",
        created_by=uuid.uuid4(),
        created_at=now,
    )


@pytest.mark.asyncio
async def test_download_returns_signed_url() -> None:
    """Happy path: signed URL returned with future expiry."""
    attachment = _make_attachment()
    use_case = DownloadAttachment(
        attachment_repo=FakeAttachmentRepository([attachment]),
        storage=FakeAttachmentStorage(),
    )  # type: ignore[arg-type]

    dto = await use_case.execute(attachment_id=attachment.id)

    assert dto.url.startswith("http://test-cdn/")
    assert "sig=fake" in dto.url
    assert dto.expires_at > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_download_nonexistent_raises() -> None:
    use_case = DownloadAttachment(
        attachment_repo=FakeAttachmentRepository(),
        storage=FakeAttachmentStorage(),
    )  # type: ignore[arg-type]

    with pytest.raises(AttachmentNotFoundError):
        await use_case.execute(attachment_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_download_allowed_for_archived_doc() -> None:
    """US-10 AC: download is allowed even when the parent document is archived.

    DownloadAttachment does not check document status — only attachment existence.
    """
    attachment = _make_attachment()
    use_case = DownloadAttachment(
        attachment_repo=FakeAttachmentRepository([attachment]),
        storage=FakeAttachmentStorage(),
    )  # type: ignore[arg-type]

    # No exception means the use case does not gate on document status
    dto = await use_case.execute(attachment_id=attachment.id)
    assert dto.url
