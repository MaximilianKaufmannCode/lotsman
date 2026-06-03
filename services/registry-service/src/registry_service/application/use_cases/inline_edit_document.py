# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-4: Inline-edit a single document field (single-field PATCH).

v1.25.0: full edit scope. Beside the original simple-scalar fields, the use
case now handles two structural updates:

  • type_code change — load new type's custom_field_schema and DROP any
    orphan custom_field_values keys that are no longer declared. The cleanup
    happens transactionally; if the document had cf values that don't fit the
    new type they vanish, with the audit trail still showing the type change
    (orphan removal is reflected in a subsequent DocumentUpdated event for
    custom_field_values if values actually changed).

  • custom_field_values change — replace the dict wholesale (caller sends
    the full new object). No schema validation here beyond what FE provided;
    backend layer trusts SPA but filters against schema if `type_code` was
    just changed to ensure consistency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from registry_service.application.dto import DocumentDTO, PatchDocumentCommand
from registry_service.application.ports import (
    Clock,
    DocumentRepository,
    DocumentTypeRepository,
    EventOutbox,
)
from registry_service.domain.errors import DocumentNotFoundError, RequiredFieldMissingError
from registry_service.domain.events import DocumentUpdated
from registry_service.domain.policies import compute_status

# Fields that cannot be set to None/empty via inline edit
_REQUIRED_FIELDS = {"asset_id", "type_code"}

# Allowed patchable fields (whitelist to prevent mass assignment).
# v1.25.0: added asset_id, custom_field_values for full edit scope.
_PATCHABLE_FIELDS = {
    "asset_id",
    "type_code",
    "number",
    "issue_date",
    "expiry_date",
    "responsible_user_id",
    "notes",
    "custom_field_values",
}


@dataclass(slots=True)
class InlineEditDocument:
    repo: DocumentRepository
    outbox: EventOutbox
    clock: Clock
    type_repo: DocumentTypeRepository | None = None

    async def execute(self, *, cmd: PatchDocumentCommand) -> DocumentDTO:
        doc = await self.repo.get_by_id(cmd.document_id)
        if doc is None:
            raise DocumentNotFoundError

        if cmd.field not in _PATCHABLE_FIELDS:
            raise RequiredFieldMissingError(f"Field '{cmd.field}' is not patchable via inline edit")

        if cmd.field in _REQUIRED_FIELDS and not cmd.value:
            raise RequiredFieldMissingError(
                f"Field '{cmd.field}' is required and cannot be set to empty"
            )

        before: Any = getattr(doc, cmd.field)
        setattr(doc, cmd.field, cmd.value)

        # v1.25.0 — When type_code changes, prune custom_field_values against the
        # new type's schema. Orphan keys (declared on the OLD type but absent in
        # the NEW one) are silently dropped per the design decision recorded in
        # CHANGELOG / docs. If `type_repo` is not wired the cleanup is skipped
        # (callers that don't supply it accept the legacy laissez-faire shape).
        cf_after_cleanup: dict[str, Any] | None = None
        if cmd.field == "type_code" and self.type_repo is not None and cmd.value:
            new_type = await self.type_repo.get_by_code(str(cmd.value))
            if new_type is not None:
                allowed_keys = {f.key for f in (new_type.custom_field_schema or [])}
                current_cf = dict(doc.custom_field_values or {})
                pruned = {k: v for k, v in current_cf.items() if k in allowed_keys}
                if pruned != current_cf:
                    doc.custom_field_values = pruned
                    cf_after_cleanup = pruned

        doc.updated_by = cmd.actor_id
        doc.updated_at = self.clock.now()

        await self.repo.update(doc)

        # Emit update event in the same transaction (enforced by the adapter)
        event = DocumentUpdated(
            document_id=doc.id,
            field=cmd.field,
            before=before,
            after=cmd.value,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=doc.updated_at,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        # v1.25.0 — Emit a secondary DocumentUpdated for the cf-cleanup so the
        # audit trail shows the orphan-drop explicitly.
        if cf_after_cleanup is not None:
            cleanup_event = DocumentUpdated(
                document_id=doc.id,
                field="custom_field_values",
                before=before if cmd.field == "custom_field_values" else None,
                after=cf_after_cleanup,
                actor_id=cmd.actor_id,
                request_id=cmd.request_id,
                occurred_at=doc.updated_at,
            )
            await self.outbox.publish(cleanup_event.as_envelope(), topic=cleanup_event.topic)

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
