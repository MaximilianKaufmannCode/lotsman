# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-7: Restore an archived document (admin-only). Idempotent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.ports import Clock, DocumentRepository, EventOutbox
from registry_service.domain.errors import DocumentNotFoundError
from registry_service.domain.events import DocumentRestored


@dataclass(slots=True)
class RestoreDocument:
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

        # Idempotent: already active → no state change, no outbox event (US-7 AC)
        if doc.deleted_at is None:
            return

        now = self.clock.now()
        doc.deleted_at = None
        doc.status = "active"
        doc.updated_by = actor_id
        doc.updated_at = now

        await self.repo.update(doc)

        event = DocumentRestored(
            document_id=doc.id,
            actor_id=actor_id,
            request_id=request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)
