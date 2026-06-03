# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for InlineEditDocument use case (US-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from registry_service.application.dto import PatchDocumentCommand
from registry_service.application.use_cases.inline_edit_document import InlineEditDocument
from registry_service.domain.entities import CustomField, Document, DocumentType
from registry_service.domain.errors import DocumentNotFoundError, RequiredFieldMissingError
from tests.unit.use_cases.fakes import (
    FakeClock,
    FakeDocumentRepository,
    FakeDocumentTypeRepository,
    FakeEventOutbox,
)


def _make_doc() -> Document:
    now = datetime.now(tz=UTC)
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        type_code="contract",
        number="DOC-001",
        issue_date=None,
        expiry_date=None,
        responsible_user_id=None,
        status="active",
        notes=None,
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_patch_number_happy_path() -> None:
    doc = _make_doc()
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = InlineEditDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="number",
            value="DOC-UPDATED-999",
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.number == "DOC-UPDATED-999"

    stored = await repo.get_by_id(doc.id)
    assert stored is not None
    assert stored.number == "DOC-UPDATED-999"

    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.document.updated.v1"
    assert topic == "registry.documents"


@pytest.mark.asyncio
async def test_patch_expiry_date() -> None:
    doc = _make_doc()
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="expiry_date",
            value=date(2030, 12, 31),
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.expiry_date == date(2030, 12, 31)


@pytest.mark.asyncio
async def test_patch_notes_to_none_is_allowed() -> None:
    """Notes is optional; setting it to None must succeed."""
    doc = _make_doc()
    doc.notes = "original note"
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="notes",
            value=None,
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.notes is None


@pytest.mark.asyncio
async def test_patch_nonexistent_document_raises() -> None:
    repo = FakeDocumentRepository()
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(DocumentNotFoundError):
        await use_case.execute(
            cmd=PatchDocumentCommand(
                document_id=uuid.uuid4(),
                field="number",
                value="X",
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_patch_non_patchable_field_raises() -> None:
    """Patching a field not in the whitelist must raise RequiredFieldMissingError."""
    doc = _make_doc()
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(RequiredFieldMissingError):
        await use_case.execute(
            cmd=PatchDocumentCommand(
                document_id=doc.id,
                field="status",  # status is computed; not patchable via inline edit
                value="archived",
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_patch_asset_id() -> None:
    """v1.25.0 — changing the contractor (asset_id) is now allowed."""
    doc = _make_doc()
    new_asset = uuid.uuid4()
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = InlineEditDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="asset_id",
            value=new_asset,
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.asset_id == new_asset
    assert len(outbox.published) == 1


@pytest.mark.asyncio
async def test_patch_custom_field_values_whole_replace() -> None:
    """v1.25.0 — full-dict replacement of custom_field_values."""
    doc = _make_doc()
    doc.custom_field_values = {"a": "1", "b": "2"}
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    new_cf = {"a": "999", "c": "3"}
    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="custom_field_values",
            value=new_cf,
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.custom_field_values == new_cf
    stored = await repo.get_by_id(doc.id)
    assert stored is not None
    assert stored.custom_field_values == new_cf


@pytest.mark.asyncio
async def test_patch_type_code_prunes_orphan_cf_keys() -> None:
    """v1.25.0 — switching type_code drops cf keys that aren't in the new schema."""
    doc = _make_doc()
    doc.type_code = "contract"
    doc.custom_field_values = {"clause": "X", "vendor_inn": "1234567890", "deprecated": "stale"}

    new_type = DocumentType(
        code="license",
        display_name="Лицензия",
        pre_notice_days=[30, 7, 1],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        custom_field_schema=[
            CustomField(key="clause", display_name="Пункт", type="text"),
            CustomField(key="region", display_name="Регион", type="text"),
        ],
    )

    repo = FakeDocumentRepository([doc])
    type_repo = FakeDocumentTypeRepository([new_type])
    outbox = FakeEventOutbox()
    use_case = InlineEditDocument(
        repo=repo, outbox=outbox, clock=FakeClock(), type_repo=type_repo  # type: ignore[arg-type]
    )

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="type_code",
            value="license",
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.type_code == "license"
    # `clause` survives (declared on new type); `vendor_inn` + `deprecated` dropped
    assert dto.custom_field_values == {"clause": "X"}

    # Two audit events: type_code change + cf cleanup
    assert len(outbox.published) == 2
    type_envelope, _ = outbox.published[0]
    cleanup_envelope, _ = outbox.published[1]
    assert type_envelope.payload["field"] == "type_code"
    assert cleanup_envelope.payload["field"] == "custom_field_values"
    assert cleanup_envelope.payload["after"] == {"clause": "X"}


@pytest.mark.asyncio
async def test_patch_type_code_no_cleanup_when_all_keys_valid() -> None:
    """If all current cf keys exist in the new type's schema, no cleanup event is emitted."""
    doc = _make_doc()
    doc.type_code = "contract"
    doc.custom_field_values = {"clause": "X"}

    new_type = DocumentType(
        code="license",
        display_name="Лицензия",
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        custom_field_schema=[CustomField(key="clause", display_name="Пункт", type="text")],
    )

    repo = FakeDocumentRepository([doc])
    type_repo = FakeDocumentTypeRepository([new_type])
    outbox = FakeEventOutbox()
    use_case = InlineEditDocument(
        repo=repo, outbox=outbox, clock=FakeClock(), type_repo=type_repo  # type: ignore[arg-type]
    )

    await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="type_code",
            value="license",
            actor_id=uuid.uuid4(),
        )
    )

    # Only the type_code event — no secondary cleanup event
    assert len(outbox.published) == 1
    assert outbox.published[0][0].payload["field"] == "type_code"


@pytest.mark.asyncio
async def test_patch_required_field_to_empty_raises() -> None:
    """Setting type_code to '' must raise RequiredFieldMissingError."""
    doc = _make_doc()
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(RequiredFieldMissingError):
        await use_case.execute(
            cmd=PatchDocumentCommand(
                document_id=doc.id,
                field="type_code",
                value="",  # empty string on required field
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_patch_updates_updated_at() -> None:
    """updated_at must advance to clock.now() after a successful patch."""
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    doc = _make_doc()
    original_updated_at = doc.updated_at
    repo = FakeDocumentRepository([doc])
    use_case = InlineEditDocument(
        repo=repo, outbox=FakeEventOutbox(), clock=FakeClock(fixed_dt=now)
    )  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=PatchDocumentCommand(
            document_id=doc.id,
            field="number",
            value="NEW",
            actor_id=uuid.uuid4(),
        )
    )

    assert dto.updated_at == now
    # Must have changed from original
    assert dto.updated_at != original_updated_at or now == original_updated_at
