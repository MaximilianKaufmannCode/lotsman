# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-9: Upload an attachment to a document."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from registry_service.application.dto import AttachmentDTO, UploadAttachmentCommand
from registry_service.application.policies import attachment_policy
from registry_service.application.ports import (
    AttachmentRepository,
    AttachmentStorage,
    Clock,
    DocumentRepository,
    EventOutbox,
    MimeSniffer,
)
from registry_service.domain.entities import Attachment
from registry_service.domain.errors import (
    AttachmentArchivedDocumentError,
    DocumentNotFoundError,
)
from registry_service.domain.events import AttachmentUploaded


def _virus_scan_stub(data: bytes) -> None:
    """Virus-scan stub (day-one contract).

    the integrations layer will replace this with a real ClamAV/connector call.
    Raises AttachmentVirusScanError if the file is flagged.
    """
    # Stub: never flags anything — safe on day one.
    # When real scan is wired, call the scanner here and raise if positive.
    _ = data


@dataclass(slots=True)
class UploadAttachment:
    doc_repo: DocumentRepository
    attachment_repo: AttachmentRepository
    storage: AttachmentStorage
    mime_sniffer: MimeSniffer
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: UploadAttachmentCommand) -> AttachmentDTO:
        # 1. Verify document exists
        doc = await self.doc_repo.get_by_id(cmd.document_id)
        if doc is None:
            raise DocumentNotFoundError

        # 2. Cannot upload to archived document (US-9 AC)
        if doc.deleted_at is not None:
            raise AttachmentArchivedDocumentError

        # 3. Size check (before MIME sniff — fail fast, no bytes to disk)
        attachment_policy.validate(cmd.content_type, len(cmd.data))

        # 4. Server-side MIME sniff (US-9: trust bytes, not extension)
        sniffed_mime = self.mime_sniffer.sniff(cmd.data)
        attachment_policy.validate(sniffed_mime, len(cmd.data))

        # 5. Virus scan stub
        _virus_scan_stub(cmd.data)

        # 6. Compute SHA-256
        sha256 = hashlib.sha256(cmd.data).hexdigest()

        # 7. Persist file
        now = self.clock.now()
        import uuid as _uuid

        attachment_id = _uuid.uuid4()
        storage_path = await self.storage.save(
            data=cmd.data,
            document_id=cmd.document_id,
            attachment_id=attachment_id,
            original_filename=cmd.filename,
        )

        # 8. Insert attachment row
        attachment = Attachment(
            id=attachment_id,
            document_id=cmd.document_id,
            original_filename=cmd.filename,
            mime_type=sniffed_mime,
            size_bytes=len(cmd.data),
            sha256=sha256,
            storage_path=storage_path,
            created_by=cmd.actor_id,
            created_at=now,
        )
        await self.attachment_repo.add(attachment)

        # 9. Emit event (modelled as document.updated per spec note US-9)
        event = AttachmentUploaded(
            attachment_id=attachment.id,
            document_id=cmd.document_id,
            mime_type=sniffed_mime,
            size_bytes=attachment.size_bytes,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return AttachmentDTO(
            id=attachment.id,
            document_id=attachment.document_id,
            original_filename=attachment.original_filename,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
            sha256=attachment.sha256,
            created_by=attachment.created_by,
            created_at=attachment.created_at,
        )
