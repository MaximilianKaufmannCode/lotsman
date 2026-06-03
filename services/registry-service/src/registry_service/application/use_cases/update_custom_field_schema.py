# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Update the custom_field_schema for a specific document type.

Responsibilities:
- Validate incoming schema (no duplicate keys, enum rules, key regex, display_name length).
- FOR UPDATE lock to serialise concurrent edits.
- Cascade-drop removed field keys from all documents of this type (in same transaction).
- Persist updated schema.
- Emit audit event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from registry_service.application.dto import UpdateCustomFieldSchemaCommand
from registry_service.application.ports import Clock, DocumentTypeRepository, EventOutbox
from registry_service.domain.custom_fields import CustomField, CustomFieldValidationError
from registry_service.domain.errors import (
    CustomFieldSchemaValidationError,
    DocumentTypeNotFoundError,
)
from registry_service.domain.events import DocumentTypeFieldsUpdated

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(slots=True)
class UpdateCustomFieldSchema:
    repo: DocumentTypeRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: UpdateCustomFieldSchemaCommand) -> list[CustomField]:
        now = self.clock.now()

        # Lock row to prevent concurrent schema mutations
        doc_type = await self.repo.get_by_code_for_update(cmd.type_code)
        if doc_type is None:
            raise DocumentTypeNotFoundError(f"Document type '{cmd.type_code}' not found")

        # Validate new schema
        new_schema = self._validate_schema(cmd.schema)

        # Compute removed keys (old - new)
        old_keys = {f.key for f in doc_type.custom_field_schema}
        new_keys = {f.key for f in new_schema}
        removed_keys = sorted(old_keys - new_keys)

        # Guard: refuse to change a field's TYPE while documents already
        # carry a value for that field — old values would silently fail
        # validation on next read. Admin must clear the values (drop +
        # re-add field, or edit each document) first.
        old_by_key = {f.key: f for f in doc_type.custom_field_schema}
        for nf in new_schema:
            of = old_by_key.get(nf.key)
            if of is None:
                continue
            if of.type != nf.type:
                count = await self.repo.count_documents_with_field(
                    cmd.type_code, nf.key
                )
                if count > 0:
                    raise CustomFieldSchemaValidationError(
                        f"Нельзя сменить тип поля '{of.display_name}' "
                        f"({of.type} → {nf.type}): {count} "
                        "документ(ов) уже содержат значения этого поля. "
                        "Сначала очистите значения у этих документов."
                    )

        schema_before = [f.to_dict() for f in doc_type.custom_field_schema]
        schema_after = [f.to_dict() for f in new_schema]

        # Cascade-drop removed keys from all documents in the same transaction
        for key in removed_keys:
            await self.repo.drop_custom_field_from_documents(cmd.type_code, key)

        # Update entity and persist
        doc_type.custom_field_schema = new_schema
        doc_type.updated_at = now
        await self.repo.upsert(doc_type)

        # Emit audit event
        event = DocumentTypeFieldsUpdated(
            type_code=cmd.type_code,
            schema_before=schema_before,
            schema_after=schema_after,
            removed_keys=removed_keys,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return new_schema

    @staticmethod
    def _validate_schema(raw_schema: list[Any]) -> list[CustomField]:
        """Build and validate CustomField list. Raises CustomFieldSchemaValidationError."""
        fields: list[CustomField] = []
        seen_keys: set[str] = set()

        for item in raw_schema:
            # item should already be a CustomField if coming from internal code,
            # or a dict if deserialised from JSON body
            if isinstance(item, CustomField):
                cf = item
            else:
                try:
                    cf = CustomField.from_dict(item)
                except (KeyError, ValueError, CustomFieldValidationError) as exc:
                    raise CustomFieldSchemaValidationError(str(exc)) from exc

            try:
                # Trigger __post_init__ validation by reconstructing
                cf = CustomField(
                    key=cf.key,
                    display_name=cf.display_name,
                    type=cf.type,
                    required=cf.required,
                    options=cf.options,
                )
            except CustomFieldValidationError as exc:
                raise CustomFieldSchemaValidationError(str(exc)) from exc

            if cf.key in seen_keys:
                raise CustomFieldSchemaValidationError(f"Duplicate field key '{cf.key}' in schema")
            seen_keys.add(cf.key)
            fields.append(cf)

        return fields
