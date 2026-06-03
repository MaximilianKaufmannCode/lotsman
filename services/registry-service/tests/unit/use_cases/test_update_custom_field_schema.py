# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for UpdateCustomFieldSchema use case."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import UpdateCustomFieldSchemaCommand
from registry_service.application.use_cases.update_custom_field_schema import (
    UpdateCustomFieldSchema,
)
from registry_service.domain.custom_fields import CustomField, FieldType
from registry_service.domain.entities import DocumentType
from registry_service.domain.errors import (
    CustomFieldSchemaValidationError,
    DocumentTypeNotFoundError,
)
from registry_service.domain.events import TOPIC_DOCUMENT_TYPES
from tests.unit.use_cases.fakes import FakeClock, FakeDocumentTypeRepository, FakeEventOutbox


def _make_doc_type(
    code: str = "license",
    schema: list[CustomField] | None = None,
) -> DocumentType:
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    return DocumentType(
        code=code,
        display_name="License",
        pre_notice_days=[30, 7],
        notify_in_day=True,
        overdue_every_days=7,
        custom_field_schema=schema or [],
        created_at=now,
        updated_at=now,
    )


class TestUpdateCustomFieldSchemaHappyPath:
    @pytest.mark.asyncio
    async def test_set_new_schema(self) -> None:
        dt = _make_doc_type()
        repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()
        clock = FakeClock()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=clock)

        new_schema = [
            CustomField(key="issuer", display_name="Issuer", type=FieldType.TEXT),
        ]
        result = await use_case.execute(
            cmd=UpdateCustomFieldSchemaCommand(
                type_code="license",
                schema=new_schema,
                actor_id=uuid.uuid4(),
            )
        )

        assert len(result) == 1
        assert result[0].key == "issuer"

    @pytest.mark.asyncio
    async def test_audit_event_emitted(self) -> None:
        dt = _make_doc_type()
        repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()
        clock = FakeClock()
        actor = uuid.uuid4()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=clock)

        await use_case.execute(
            cmd=UpdateCustomFieldSchemaCommand(
                type_code="license",
                schema=[
                    CustomField(key="ref", display_name="Ref", type=FieldType.TEXT, options=None)
                ],
                actor_id=actor,
            )
        )

        assert len(outbox.published) == 1
        envelope, topic = outbox.published[0]
        assert topic == TOPIC_DOCUMENT_TYPES
        assert envelope.type == "registry.document_type.fields_updated.v1"
        payload = envelope.payload
        assert payload["type_code"] == "license"
        assert payload["actor_id"] == str(actor)
        assert isinstance(payload["schema_after"], list)

    @pytest.mark.asyncio
    async def test_removed_keys_cascade(self) -> None:
        """Removing a field key should cascade-drop it from documents via repo call."""
        old_field = CustomField(key="old_key", display_name="Old", type=FieldType.TEXT)
        dt = _make_doc_type(schema=[old_field])
        repo = FakeDocumentTypeRepository(types=[dt])
        # Patch repo to track drop calls
        dropped: list[tuple[str, str]] = []

        async def tracking_drop(type_code: str, field_key: str) -> None:
            dropped.append((type_code, field_key))

        repo.drop_custom_field_from_documents = tracking_drop  # type: ignore[method-assign]
        outbox = FakeEventOutbox()
        clock = FakeClock()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=clock)

        # New schema has no fields → old_key is removed
        await use_case.execute(
            cmd=UpdateCustomFieldSchemaCommand(
                type_code="license",
                schema=[],
                actor_id=uuid.uuid4(),
            )
        )

        assert ("license", "old_key") in dropped
        # Audit event should include removed_keys
        envelope, _ = outbox.published[0]
        assert "old_key" in envelope.payload["removed_keys"]

    @pytest.mark.asyncio
    async def test_audit_event_removed_keys_empty_when_no_removals(self) -> None:
        dt = _make_doc_type()
        repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=FakeClock())

        await use_case.execute(
            cmd=UpdateCustomFieldSchemaCommand(
                type_code="license",
                schema=[CustomField(key="new_field", display_name="New", type=FieldType.NUMBER)],
                actor_id=uuid.uuid4(),
            )
        )

        envelope, _ = outbox.published[0]
        assert envelope.payload["removed_keys"] == []


class TestUpdateCustomFieldSchemaErrors:
    @pytest.mark.asyncio
    async def test_type_not_found(self) -> None:
        repo = FakeDocumentTypeRepository(types=[])
        outbox = FakeEventOutbox()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=FakeClock())

        with pytest.raises(DocumentTypeNotFoundError):
            await use_case.execute(
                cmd=UpdateCustomFieldSchemaCommand(
                    type_code="nonexistent",
                    schema=[],
                    actor_id=uuid.uuid4(),
                )
            )

    @pytest.mark.asyncio
    async def test_duplicate_keys_rejected(self) -> None:
        dt = _make_doc_type()
        repo = FakeDocumentTypeRepository(types=[dt])
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())

        duplicate_schema = [
            CustomField(key="dupe", display_name="Dupe A", type=FieldType.TEXT),
            CustomField(key="dupe", display_name="Dupe B", type=FieldType.TEXT),
        ]

        with pytest.raises(CustomFieldSchemaValidationError, match="Duplicate"):
            await use_case.execute(
                cmd=UpdateCustomFieldSchemaCommand(
                    type_code="license",
                    schema=duplicate_schema,
                    actor_id=uuid.uuid4(),
                )
            )

    @pytest.mark.asyncio
    async def test_enum_without_options_rejected(self) -> None:
        dt = _make_doc_type()
        repo = FakeDocumentTypeRepository(types=[dt])
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())

        # Pass raw dict so _validate_schema catches the error (not the call site)
        bad_schema_as_dicts = [
            {"key": "status", "display_name": "Status", "type": "enum", "required": False}
            # "options" omitted → should fail enum validation
        ]

        with pytest.raises(CustomFieldSchemaValidationError):
            await use_case.execute(
                cmd=UpdateCustomFieldSchemaCommand(
                    type_code="license",
                    schema=bad_schema_as_dicts,  # type: ignore[arg-type]
                    actor_id=uuid.uuid4(),
                )
            )
