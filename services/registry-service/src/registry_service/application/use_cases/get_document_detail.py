# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-8: Get document detail (single document with attachments)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from registry_service.application.dto import AttachmentDTO, DocumentDTO
from registry_service.application.ports import AttachmentRepository, Clock, DocumentRepository
from registry_service.domain.errors import DocumentNotFoundError
from registry_service.domain.policies import compute_status


@dataclass(slots=True)
class GetDocumentDetail:
    doc_repo: DocumentRepository
    attachment_repo: AttachmentRepository
    clock: Clock

    async def execute(
        self,
        *,
        document_id: uuid.UUID,
    ) -> tuple[DocumentDTO, list[AttachmentDTO]]:
        today: date = self.clock.today()
        doc = await self.doc_repo.get_by_id(document_id)
        if doc is None:
            raise DocumentNotFoundError

        urgency = compute_status(doc.expiry_date, doc.deleted_at, today)
        doc_dto = DocumentDTO(
            id=doc.id,
            asset_id=doc.asset_id,
            type_code=doc.type_code,
            number=doc.number,
            issue_date=doc.issue_date,
            expiry_date=doc.expiry_date,
            responsible_user_id=doc.responsible_user_id,
            status=doc.status,
            urgency_status=urgency.value,
            notes=doc.notes,
            created_by=doc.created_by,
            updated_by=doc.updated_by,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            deleted_at=doc.deleted_at,
            custom_field_values=doc.custom_field_values,
        )

        attachments = await self.attachment_repo.list_for_document(document_id)
        att_dtos = [
            AttachmentDTO(
                id=a.id,
                document_id=a.document_id,
                original_filename=a.original_filename,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                sha256=a.sha256,
                created_by=a.created_by,
                created_at=a.created_at,
            )
            for a in attachments
        ]

        return doc_dto, att_dtos
