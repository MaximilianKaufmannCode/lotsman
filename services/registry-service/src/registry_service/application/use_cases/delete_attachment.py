# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-11: Hard-delete an attachment. Not idempotent (404 on second call)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.ports import (
    AttachmentRepository,
    AttachmentStorage,
    Clock,
    DocumentRepository,
    EventOutbox,
)
from registry_service.domain.errors import (
    AttachmentDeleteArchivedDocumentError,
    AttachmentNotFoundError,
)
from registry_service.domain.events import AttachmentDeleted


@dataclass(slots=True)
class DeleteAttachment:
    attachment_repo: AttachmentRepository
    doc_repo: DocumentRepository
    storage: AttachmentStorage
    outbox: EventOutbox
    clock: Clock

    async def execute(
        self,
        *,
        attachment_id: uuid.UUID,
        actor_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        attachment = await self.attachment_repo.get_by_id(attachment_id)
        if attachment is None:
            raise AttachmentNotFoundError

        # Cannot delete from archived document (US-11 AC)
        doc = await self.doc_repo.get_by_id(attachment.document_id)
        if doc is not None and doc.deleted_at is not None:
            raise AttachmentDeleteArchivedDocumentError

        now = self.clock.now()

        # Hard delete DB row first (so the outbox event is in the same transaction)
        await self.attachment_repo.delete(attachment_id)

        event = AttachmentDeleted(
            attachment_id=attachment_id,
            document_id=attachment.document_id,
            actor_id=actor_id,
            request_id=request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        # Queue file removal after transaction commits (fire-and-forget on error is acceptable
        # — orphaned files are cleaned by a periodic maintenance job)
        await self.storage.delete(attachment.storage_path)
