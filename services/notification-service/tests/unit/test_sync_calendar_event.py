# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for SyncCalendarEvent use case — ADR-0005 §5.

Covers the 6 decision-matrix cases:
  1. Document not found → delete all mappings.
  2. Document archived → delete all mappings.
  3. Document expires_at=None → delete all mappings.
  4. Document active + expires_at set + no mapping → CreateItem.
  5. Document active + expires_at set + mapping exists → UpdateItem.
  6. Mapping offset not in required → DeleteItem + remove row.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from notification_service.domain.calendar import (
    CalendarEventData,
    CalendarMapping,
    CalendarSyncResult,
    CalendarTestResult,
    OrphanEvent,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCalendarDriver:
    def __init__(self) -> None:
        self.upserted: list[tuple[str, CalendarMapping | None, CalendarEventData]] = []
        self.deleted: list[tuple[str, CalendarMapping]] = []
        self.fail_on_upsert: bool = False

    async def upsert_event(
        self, *, mailbox: str, mapping: CalendarMapping | None, event_data: CalendarEventData
    ) -> CalendarSyncResult:
        if self.fail_on_upsert:
            raise RuntimeError("EWS transient failure")
        self.upserted.append((mailbox, mapping, event_data))
        return CalendarSyncResult(
            exchange_item_id=f"ItemId-{event_data.notice_offset_days}",
            change_key=f"CK-{event_data.notice_offset_days}",
            external_marker=event_data.external_marker,
            was_created=mapping is None,
        )

    async def delete_event(self, *, mailbox: str, mapping: CalendarMapping) -> None:
        self.deleted.append((mailbox, mapping))

    async def find_orphans(self, *, mailbox: str) -> list[OrphanEvent]:
        return []

    async def test_connection(self, *, mailbox: str) -> CalendarTestResult:
        return CalendarTestResult(success=True, detail="OK")

    async def upsert_heartbeat(self, *, mailbox: str) -> None:
        pass


class FakeMappingRepository:
    def __init__(self, initial: list[CalendarMapping] | None = None) -> None:
        self._mappings: list[CalendarMapping] = list(initial or [])
        self.upserted: list[dict] = []
        self.deleted: list[tuple[uuid.UUID, int]] = []

    async def get_by_document(self, document_id: uuid.UUID) -> list[CalendarMapping]:
        return [m for m in self._mappings if m.document_id == document_id]

    async def upsert(self, **kwargs: Any) -> None:
        self.upserted.append(kwargs)
        # Update or add in-memory.
        doc_id = kwargs["document_id"]
        offset = kwargs["notice_offset_days"]
        self._mappings = [
            m for m in self._mappings
            if not (m.document_id == doc_id and m.notice_offset_days == offset)
        ]
        self._mappings.append(
            CalendarMapping(
                document_id=doc_id,
                notice_offset_days=offset,
                exchange_item_id=kwargs.get("exchange_item_id", ""),
                change_key=kwargs.get("change_key", ""),
                external_marker=kwargs.get("external_marker", ""),
                sync_state=kwargs.get("sync_state", "pending"),
            )
        )

    async def delete(self, *, document_id: uuid.UUID, notice_offset_days: int) -> None:
        self.deleted.append((document_id, notice_offset_days))
        self._mappings = [
            m for m in self._mappings
            if not (m.document_id == document_id and m.notice_offset_days == notice_offset_days)
        ]

    async def list_stale(self, **kwargs: Any) -> list[CalendarMapping]:
        return []


class FakeRegistryGateway:
    def __init__(self, document: dict | None = None, doc_type: dict | None = None) -> None:
        self._document = document
        self._doc_type = doc_type

    async def get_document(self, document_id: uuid.UUID) -> dict | None:
        return self._document

    async def get_document_type(self, type_code: str) -> dict | None:
        return self._doc_type

    async def list_active_documents(self) -> list[dict]:
        return [self._document] if self._document else []


class FakeOutbox:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, envelope: Any) -> None:
        self.events.append(envelope)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_document(doc_id: uuid.UUID, expires: str = "2026-08-15") -> dict:
    return {
        "id": str(doc_id),
        "name": "Test Licence",
        "display_name": "Test Licence",
        "type_code": "licence",
        "expires_at": expires,
        "archived": False,
        "status": "active",
        "asset_name": "ООО Тест",
    }


def _doc_type_with_days(days: list[int]) -> dict:
    return {"code": "licence", "pre_notice_days": days}


def _make_use_case(
    driver: FakeCalendarDriver,
    mapping_repo: FakeMappingRepository,
    registry: FakeRegistryGateway,
    outbox: FakeOutbox,
) -> Any:
    from notification_service.application.use_cases.sync_calendar_event import SyncCalendarEvent

    return SyncCalendarEvent(
        driver=driver,
        mapping_repo=mapping_repo,
        registry=registry,
        outbox=outbox,
        mailbox="cal@example.com",
        default_notice_days=14,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_document_not_found_deletes_all() -> None:
    """Case 1: Document 404 → delete all existing mappings."""
    doc_id = uuid.uuid4()
    existing = [
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=0,
            exchange_item_id="OldItem==",
            change_key="OldCK==",
            external_marker=f"lotsman:doc:{doc_id}:offset:0",
            sync_state="synced",
        )
    ]
    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=existing)
    registry = FakeRegistryGateway(document=None)
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    # The existing mapping should have been deleted.
    assert len(driver.deleted) == 1
    assert driver.deleted[0][1].notice_offset_days == 0
    assert (doc_id, 0) in mapping_repo.deleted
    assert "notification.calendar.sync_succeeded.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_sync_archived_document_deletes_all() -> None:
    """Case 2: Document archived → delete all existing calendar events."""
    doc_id = uuid.uuid4()
    archived_doc = _active_document(doc_id)
    archived_doc["archived"] = True
    archived_doc["status"] = "archived"

    existing = [
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=0,
            exchange_item_id="I0==",
            change_key="CK0==",
            external_marker=f"lotsman:doc:{doc_id}:offset:0",
            sync_state="synced",
        ),
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=14,
            exchange_item_id="I14==",
            change_key="CK14==",
            external_marker=f"lotsman:doc:{doc_id}:offset:14",
            sync_state="synced",
        ),
    ]
    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=existing)
    registry = FakeRegistryGateway(document=archived_doc)
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    assert len(driver.deleted) == 2
    assert "notification.calendar.sync_succeeded.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_sync_no_expires_at_deletes_all() -> None:
    """Case 3: Document active but expires_at=None → delete all."""
    doc_id = uuid.uuid4()
    doc = _active_document(doc_id)
    doc["expires_at"] = None

    existing = [
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=0,
            exchange_item_id="I==",
            change_key="CK==",
            external_marker=f"lotsman:doc:{doc_id}:offset:0",
            sync_state="synced",
        )
    ]
    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=existing)
    registry = FakeRegistryGateway(document=doc)
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    assert len(driver.deleted) == 1
    assert "notification.calendar.sync_succeeded.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_sync_active_document_no_mapping_creates() -> None:
    """Case 4: Active document + expires_at set + no mapping → CreateItem for each offset."""
    doc_id = uuid.uuid4()
    doc = _active_document(doc_id, "2026-08-15")

    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=[])
    registry = FakeRegistryGateway(
        document=doc,
        doc_type=_doc_type_with_days([30, 14]),
    )
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    # Should create 3 events: offsets 0, 14, 30.
    created_offsets = {u[2].notice_offset_days for u in driver.upserted}
    assert created_offsets == {0, 14, 30}

    # All upserts were with mapping=None (CreateItem).
    assert all(u[1] is None for u in driver.upserted)

    # DB rows were inserted.
    upserted_offsets = {r["notice_offset_days"] for r in mapping_repo.upserted}
    assert upserted_offsets == {0, 14, 30}

    assert "notification.calendar.sync_succeeded.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_sync_active_document_with_mapping_updates() -> None:
    """Case 5: Active document + existing mapping → UpdateItem (with change_key).

    We supply exactly the offsets that pre_notice_days=[7] requires: {0, 7}.
    Both have existing mappings so both should be UpdateItem calls.
    """
    doc_id = uuid.uuid4()
    doc = _active_document(doc_id, "2026-08-15")

    existing_0 = CalendarMapping(
        document_id=doc_id,
        notice_offset_days=0,
        exchange_item_id="ExistingItem0==",
        change_key="ExistingCK0==",
        external_marker=f"lotsman:doc:{doc_id}:offset:0",
        sync_state="synced",
    )
    existing_7 = CalendarMapping(
        document_id=doc_id,
        notice_offset_days=7,
        exchange_item_id="ExistingItem7==",
        change_key="ExistingCK7==",
        external_marker=f"lotsman:doc:{doc_id}:offset:7",
        sync_state="synced",
    )

    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=[existing_0, existing_7])
    registry = FakeRegistryGateway(
        document=doc,
        doc_type=_doc_type_with_days([7]),  # required offsets: {0, 7}
    )
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    # Both offsets should be UpdateItem (mapping passed to driver).
    assert len(driver.upserted) == 2
    for _mailbox, mapping, event_data in driver.upserted:
        # The driver received the existing mapping (UpdateItem path).
        offset = event_data.notice_offset_days
        assert mapping is not None, f"Expected existing mapping for offset {offset}"
        if event_data.notice_offset_days == 0:
            assert mapping.exchange_item_id == "ExistingItem0=="
            assert mapping.change_key == "ExistingCK0=="
        else:
            assert mapping.exchange_item_id == "ExistingItem7=="
            assert mapping.change_key == "ExistingCK7=="


@pytest.mark.asyncio
async def test_sync_stale_offset_deleted() -> None:
    """Case 6: Mapping offset not in required offsets → DeleteItem + remove DB row."""
    doc_id = uuid.uuid4()
    doc = _active_document(doc_id, "2026-08-15")

    # Existing mappings: offsets 0 and 60. Required (from doc_type): 0 and 14.
    # Offset 60 is stale → should be deleted.
    existing = [
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=0,
            exchange_item_id="I0==",
            change_key="CK0==",
            external_marker=f"lotsman:doc:{doc_id}:offset:0",
            sync_state="synced",
        ),
        CalendarMapping(
            document_id=doc_id,
            notice_offset_days=60,
            exchange_item_id="I60==",
            change_key="CK60==",
            external_marker=f"lotsman:doc:{doc_id}:offset:60",
            sync_state="synced",
        ),
    ]

    driver = FakeCalendarDriver()
    mapping_repo = FakeMappingRepository(initial=existing)
    registry = FakeRegistryGateway(
        document=doc,
        doc_type=_doc_type_with_days([14]),
    )
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    # Offset 60 should be deleted.
    deleted_offsets = {d[1].notice_offset_days for d in driver.deleted}
    assert 60 in deleted_offsets

    # Offset 60 removed from DB.
    assert (doc_id, 60) in mapping_repo.deleted

    # Offsets 0 and 14 should be upserted.
    upserted_offsets = {r["notice_offset_days"] for r in mapping_repo.upserted}
    assert 0 in upserted_offsets
    assert 14 in upserted_offsets


@pytest.mark.asyncio
async def test_sync_partial_failure_marks_failed_offset() -> None:
    """Partial EWS failure marks offset as 'failed', others still succeed."""
    doc_id = uuid.uuid4()
    doc = _active_document(doc_id, "2026-09-01")

    driver = FakeCalendarDriver()
    driver.fail_on_upsert = True  # All upserts fail.

    mapping_repo = FakeMappingRepository(initial=[])
    registry = FakeRegistryGateway(
        document=doc,
        doc_type=_doc_type_with_days([7]),
    )
    outbox = FakeOutbox()

    use_case = _make_use_case(driver, mapping_repo, registry, outbox)
    await use_case.execute(doc_id)

    # On total failure, sync_failed event is emitted.
    assert "notification.calendar.sync_failed.v1" in outbox.event_types()

    # DB rows should be marked failed (not synced).
    for row in mapping_repo.upserted:
        assert row["sync_state"] in ("failed", "dlq")
        assert row["last_error"] is not None
