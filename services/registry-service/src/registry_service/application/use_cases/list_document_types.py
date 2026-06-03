# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-16: List all document types."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import DocumentTypeDTO
from registry_service.application.ports import DocumentTypeRepository


@dataclass(slots=True)
class ListDocumentTypes:
    repo: DocumentTypeRepository

    async def execute(self) -> list[DocumentTypeDTO]:
        types = await self.repo.list_all()
        return [
            DocumentTypeDTO(
                code=t.code,
                display_name=t.display_name,
                pre_notice_days=t.pre_notice_days,
                notify_in_day=t.notify_in_day,
                overdue_every_days=t.overdue_every_days,
                created_at=t.created_at,
                updated_at=t.updated_at,
                custom_field_schema=[f.to_dict() for f in t.custom_field_schema],
            )
            for t in types
        ]
