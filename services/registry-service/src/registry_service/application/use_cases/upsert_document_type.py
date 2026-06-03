# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-17: Create or update a document type (admin-only)."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import DocumentTypeDTO, UpsertDocumentTypeCommand
from registry_service.application.ports import Clock, DocumentTypeRepository, EventOutbox
from registry_service.domain.entities import DocumentType
from registry_service.domain.events import DocumentTypeUpserted


@dataclass(slots=True)
class UpsertDocumentType:
    repo: DocumentTypeRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: UpsertDocumentTypeCommand) -> DocumentTypeDTO:
        now = self.clock.now()

        existing = await self.repo.get_by_code(cmd.code)
        if existing is None:
            doc_type = DocumentType.create(
                code=cmd.code,
                display_name=cmd.display_name,
                pre_notice_days=cmd.pre_notice_days,
                notify_in_day=cmd.notify_in_day,
                overdue_every_days=cmd.overdue_every_days,
                now=now,
            )
        else:
            existing.display_name = cmd.display_name
            existing.pre_notice_days = cmd.pre_notice_days
            existing.notify_in_day = cmd.notify_in_day
            existing.overdue_every_days = cmd.overdue_every_days
            existing.updated_at = now
            doc_type = existing

        await self.repo.upsert(doc_type)

        event = DocumentTypeUpserted(
            code=doc_type.code,
            display_name=doc_type.display_name,
            pre_notice_days=doc_type.pre_notice_days,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return DocumentTypeDTO(
            code=doc_type.code,
            display_name=doc_type.display_name,
            pre_notice_days=doc_type.pre_notice_days,
            notify_in_day=doc_type.notify_in_day,
            overdue_every_days=doc_type.overdue_every_days,
            created_at=doc_type.created_at,
            updated_at=doc_type.updated_at,
            custom_field_schema=[f.to_dict() for f in doc_type.custom_field_schema],
        )
