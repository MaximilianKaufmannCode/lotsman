# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Get the custom_field_schema for a specific document type."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.ports import DocumentTypeRepository
from registry_service.domain.custom_fields import CustomField
from registry_service.domain.errors import DocumentTypeNotFoundError


@dataclass(slots=True)
class GetCustomFieldSchema:
    repo: DocumentTypeRepository

    async def execute(self, *, type_code: str) -> list[CustomField]:
        doc_type = await self.repo.get_by_code(type_code)
        if doc_type is None:
            raise DocumentTypeNotFoundError(f"Document type '{type_code}' not found")
        return doc_type.custom_field_schema
