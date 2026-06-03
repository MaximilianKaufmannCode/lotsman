# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for UpsertDocumentType use case (US-17)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import UpsertDocumentTypeCommand
from registry_service.application.use_cases.upsert_document_type import UpsertDocumentType
from registry_service.domain.entities import DocumentType
from tests.unit.use_cases.fakes import FakeClock, FakeDocumentTypeRepository, FakeEventOutbox


def _make_cmd(**kwargs: object) -> UpsertDocumentTypeCommand:
    defaults: dict[str, object] = {
        "code": "contract",
        "display_name": "Договор",
        "pre_notice_days": [30, 7, 1],
        "notify_in_day": True,
        "overdue_every_days": 7,
        "actor_id": uuid.uuid4(),
    }
    defaults.update(kwargs)
    return UpsertDocumentTypeCommand(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_upsert_creates_new_type() -> None:
    """When code does not exist, a new DocumentType is created."""
    repo = FakeDocumentTypeRepository()
    outbox = FakeEventOutbox()
    use_case = UpsertDocumentType(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(cmd=_make_cmd())

    assert dto.code == "contract"
    assert dto.display_name == "Договор"
    assert dto.pre_notice_days == [30, 7, 1]
    assert dto.notify_in_day is True
    assert dto.overdue_every_days == 7

    stored = await repo.get_by_code("contract")
    assert stored is not None

    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.document_type.upserted.v1"
    assert topic == "registry.document_types"


@pytest.mark.asyncio
async def test_upsert_updates_existing_type() -> None:
    """When code already exists, the type is updated in place."""
    now = datetime.now(tz=UTC)
    existing = DocumentType(
        code="contract",
        display_name="Старое название",
        pre_notice_days=[60],
        notify_in_day=False,
        overdue_every_days=14,
        created_at=now,
        updated_at=now,
    )
    repo = FakeDocumentTypeRepository([existing])
    outbox = FakeEventOutbox()
    use_case = UpsertDocumentType(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=_make_cmd(
            code="contract",
            display_name="Договор (обновлён)",
            pre_notice_days=[30, 7],
            notify_in_day=True,
            overdue_every_days=7,
        )
    )

    assert dto.display_name == "Договор (обновлён)"
    assert dto.pre_notice_days == [30, 7]
    assert dto.notify_in_day is True
    assert dto.overdue_every_days == 7

    # created_at must be preserved
    stored = await repo.get_by_code("contract")
    assert stored is not None
    assert stored.created_at == now

    # Outbox event emitted even for update
    assert len(outbox.published) == 1


@pytest.mark.asyncio
async def test_upsert_different_codes_are_independent() -> None:
    """Upserting two different codes produces two independent entries."""
    repo = FakeDocumentTypeRepository()
    use_case = UpsertDocumentType(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(cmd=_make_cmd(code="contract", display_name="Договор"))
    await use_case.execute(cmd=_make_cmd(code="license", display_name="Лицензия"))

    all_types = await repo.list_all()
    codes = {t.code for t in all_types}
    assert codes == {"contract", "license"}
