# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-6: Soft-delete (archive) a document. Idempotent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.ports import Clock, DocumentRepository, EventOutbox
from registry_service.domain.errors import DocumentNotFoundError
from registry_service.domain.events import DocumentArchived


@dataclass(slots=True)
class ArchiveDocument:
    repo: DocumentRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(
        self,
        *,
        document_id: uuid.UUID,
        actor_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise DocumentNotFoundError

        # Idempotent: already archived → no-op, no outbox event (US-6 AC)
        if doc.deleted_at is not None:
            return

        now = self.clock.now()
        doc.deleted_at = now
        doc.status = "archived"
        doc.updated_by = actor_id
        doc.updated_at = now

        await self.repo.update(doc)

        event = DocumentArchived(
            document_id=doc.id,
            actor_id=actor_id,
            request_id=request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)
