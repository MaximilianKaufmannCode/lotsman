# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-5: Create a new document."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from registry_service.application.dto import CreateDocumentCommand, DocumentDTO
from registry_service.application.ports import (
    AssetRepository,
    Clock,
    DocumentRepository,
    DocumentTypeRepository,
    EventOutbox,
)
from registry_service.domain.custom_fields import (
    CustomFieldValidationError,
    validate_values_against_schema,
)
from registry_service.domain.entities import Document
from registry_service.domain.errors import (
    AssetArchivedError,
    CustomFieldSchemaValidationError,
    DocumentTypeNotFoundError,
)
from registry_service.domain.events import DocumentCreated
from registry_service.domain.policies import compute_status


@dataclass(slots=True)
class CreateDocument:
    doc_repo: DocumentRepository
    asset_repo: AssetRepository
    type_repo: DocumentTypeRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: CreateDocumentCommand) -> DocumentDTO:
        # Verify asset exists and is active
        asset = await self.asset_repo.get_active_by_id(cmd.asset_id)
        if asset is None:
            raise AssetArchivedError("Asset not found or archived")

        # Verify type exists
        doc_type = await self.type_repo.get_by_code(cmd.type_code)
        if doc_type is None:
            raise DocumentTypeNotFoundError

        # Validate custom field values against type schema
        try:
            validated_custom = validate_values_against_schema(
                doc_type.custom_field_schema,
                cmd.custom_field_values,
            )
        except CustomFieldValidationError as exc:
            raise CustomFieldSchemaValidationError(str(exc)) from exc

        now = self.clock.now()
        doc = Document.create(
            asset_id=cmd.asset_id,
            type_code=cmd.type_code,
            number=cmd.number,
            issue_date=cmd.issue_date,
            expiry_date=cmd.expiry_date,
            responsible_user_id=cmd.responsible_user_id,
            notes=cmd.notes,
            created_by=cmd.actor_id,
            custom_field_values=validated_custom,
            now=now,
        )

        await self.doc_repo.add(doc)

        event = DocumentCreated(
            document_id=doc.id,
            asset_id=doc.asset_id,
            type_code=doc.type_code,
            expiry_date=doc.expiry_date,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        today: date = self.clock.today()
        urgency = compute_status(doc.expiry_date, doc.deleted_at, today)
        return DocumentDTO(
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
