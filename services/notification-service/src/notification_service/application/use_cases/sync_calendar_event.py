# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SyncCalendarEvent use case — ADR-0005 §5.

Triggered by:
  - registry.document.* events via RegistryDocumentConsumer
  - ARQ retry tasks
  - WarmUpReconciliation on service start
  - DailyReconciliation cron

Decision matrix (per §5):
  - Document archived OR expires_at IS NULL → DELETE all mappings + EWS events.
  - Document active + expires_at set → upsert N events (one per offset in
    required_offsets = {0} ∪ set(pre_notice_days)).
    - Offset in required but no mapping → CreateItem.
    - Offset in required + mapping exists → UpdateItem (with change_key).
    - Mapping exists but offset NOT in required → DeleteItem + delete DB row.

Partial failure handling: each offset is processed independently.  A failure
on one offset does NOT abort the others.  Failed offsets get sync_state='failed'
with last_error and retry_count incremented.

Emits audit events: notification.calendar.sync_succeeded.v1 and
notification.calendar.sync_failed.v1.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog

from notification_service.application.ports import (
    CalendarDriver,
    CalendarEventMappingRepository,
    EventOutbox,
    RegistryDocumentGateway,
)
from notification_service.domain.calendar import (
    CalendarEventData,
    CalendarMapping,
)
from notification_service.domain.events import CalendarSyncFailed, CalendarSyncSucceeded

log = structlog.get_logger(__name__)

# Sentinel UUID for heartbeat / probe events that have no real document.
_SENTINEL_DOC_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_MAX_RETRY = 10


@dataclass(slots=True)
class SyncCalendarEvent:
    """Synchronise a single document's calendar events with Exchange."""

    driver: CalendarDriver
    mapping_repo: CalendarEventMappingRepository
    registry: RegistryDocumentGateway
    outbox: EventOutbox
    mailbox: str
    default_notice_days: int = 14
    web_bff_url: str = "https://lotsman.example.com"

    async def execute(self, document_id: uuid.UUID) -> None:
        """Main entry point called by consumer and ARQ tasks."""
        logger = log.bind(document_id=str(document_id))

        # 1. Load document from registry.
        document = await self.registry.get_document(document_id)

        # 2. Load existing mappings for this document.
        existing_mappings: list[CalendarMapping] = await self.mapping_repo.get_by_document(
            document_id
        )
        existing_by_offset: dict[int, CalendarMapping] = {
            m.notice_offset_days: m for m in existing_mappings
        }

        # 3. Decision: delete-all or upsert-N?
        if _should_delete_all(document):
            logger.info("sync_calendar.deleting_all", reason=_delete_reason(document))
            await self._delete_all(document_id, existing_mappings)
            await self.outbox.publish(
                CalendarSyncSucceeded(
                    document_id=document_id,
                    offsets_synced=[],
                    offsets_deleted=list(existing_by_offset.keys()),
                ).as_envelope()
            )
            return

        # 4. Determine required offsets.
        # At this point _should_delete_all returned False, so document is non-None.
        assert document is not None
        expires_at: date = _parse_expires_at(document)
        pre_notice_days = await self._load_pre_notice_days(document)
        required_offsets = set([0] + list(pre_notice_days))

        # 5. Determine stale offsets (in DB but not required any more).
        stale_offsets = set(existing_by_offset.keys()) - required_offsets

        # 6. Fan-out: upsert required + delete stale — parallel, partial-fail safe.
        upsert_tasks = [
            self._upsert_offset(
                document_id=document_id,
                offset=offset,
                expires_at=expires_at,
                document=document,
                mapping=existing_by_offset.get(offset),
            )
            for offset in required_offsets
        ]
        delete_tasks = [
            self._delete_offset(document_id, existing_by_offset[offset])
            for offset in stale_offsets
        ]

        results = await asyncio.gather(*upsert_tasks, *delete_tasks, return_exceptions=True)

        failed_offsets: list[int] = []
        synced_offsets: list[int] = []
        deleted_offsets = list(stale_offsets)

        for i, res in enumerate(results):
            if i < len(required_offsets):
                offset = list(required_offsets)[i]
                if isinstance(res, BaseException):
                    failed_offsets.append(offset)
                else:
                    synced_offsets.append(offset)

        if failed_offsets:
            logger.warning(
                "sync_calendar.partial_failure",
                failed_offsets=failed_offsets,
                synced_offsets=synced_offsets,
            )
            await self.outbox.publish(
                CalendarSyncFailed(
                    document_id=document_id,
                    offsets_failed=failed_offsets,
                    offsets_synced=synced_offsets,
                ).as_envelope()
            )
        else:
            logger.info(
                "sync_calendar.succeeded",
                synced_offsets=synced_offsets,
                deleted_offsets=deleted_offsets,
            )
            await self.outbox.publish(
                CalendarSyncSucceeded(
                    document_id=document_id,
                    offsets_synced=synced_offsets,
                    offsets_deleted=deleted_offsets,
                ).as_envelope()
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_pre_notice_days(self, document: dict[str, Any]) -> list[int]:
        """Load pre_notice_days from document type; fall back to default."""
        type_code = document.get("type_code")
        if type_code:
            doc_type = await self.registry.get_document_type(type_code)
            if doc_type:
                days: list[int] = doc_type.get("pre_notice_days") or []
                if days:
                    return days
        return [self.default_notice_days]

    async def _upsert_offset(
        self,
        *,
        document_id: uuid.UUID,
        offset: int,
        expires_at: date,
        document: dict[str, Any],
        mapping: CalendarMapping | None,
    ) -> None:
        """Upsert one Exchange event + DB mapping row."""
        event_date = expires_at - timedelta(days=offset)
        external_marker = f"lotsman:doc:{document_id}:offset:{offset}"
        subject = _build_subject(document, offset, expires_at)
        body = _build_body(document, offset, expires_at, self.web_bff_url)

        event_data = CalendarEventData(
            document_id=document_id,
            notice_offset_days=offset,
            event_date=event_date,
            subject=subject,
            body=body,
            external_marker=external_marker,
        )

        try:
            result = await self.driver.upsert_event(
                mailbox=self.mailbox,
                mapping=mapping,
                event_data=event_data,
            )
            await self.mapping_repo.upsert(
                document_id=document_id,
                notice_offset_days=offset,
                exchange_item_id=result.exchange_item_id,
                change_key=result.change_key,
                external_marker=external_marker,
                sync_state="synced",
                last_error=None,
                retry_count=0,
            )
        except Exception as exc:
            error_msg = str(exc)
            current_retry = 0
            if mapping is not None:
                current_retry = mapping.retry_count + 1
            new_state = "dlq" if current_retry >= _MAX_RETRY else "failed"
            await self.mapping_repo.upsert(
                document_id=document_id,
                notice_offset_days=offset,
                exchange_item_id=mapping.exchange_item_id if mapping else "",
                change_key=mapping.change_key if mapping else "",
                external_marker=external_marker,
                sync_state=new_state,
                last_error=error_msg,
                retry_count=current_retry,
            )
            raise

    async def _delete_offset(
        self, document_id: uuid.UUID, mapping: CalendarMapping
    ) -> None:
        """Delete one Exchange event + DB mapping row."""
        try:
            await self.driver.delete_event(mailbox=self.mailbox, mapping=mapping)
        except Exception:
            log.warning(
                "sync_calendar.delete_failed",
                document_id=str(document_id),
                offset=mapping.notice_offset_days,
            )
        # Always remove from DB — even if Exchange delete failed, the mapping
        # is no longer desired.  Orphan recovery (§13) handles any EWS remnants.
        await self.mapping_repo.delete(
            document_id=document_id,
            notice_offset_days=mapping.notice_offset_days,
        )

    async def _delete_all(
        self,
        document_id: uuid.UUID,
        mappings: list[CalendarMapping],
    ) -> None:
        """Delete all Exchange events + DB rows for a document."""
        tasks = [self._delete_offset(document_id, m) for m in mappings]
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _should_delete_all(document: dict[str, Any] | None) -> bool:
    """Return True if all calendar events for this document should be removed."""
    if document is None:
        return True
    if document.get("archived") or document.get("status") == "archived":
        return True
    return not document.get("expires_at") and not document.get("expiry_date")


def _delete_reason(document: dict[str, Any] | None) -> str:
    if document is None:
        return "document_not_found"
    if document.get("archived") or document.get("status") == "archived":
        return "document_archived"
    return "expires_at_null"


def _parse_expires_at(document: dict[str, Any]) -> date:
    """Extract expires_at / expiry_date from registry document dict."""
    raw = document.get("expires_at") or document.get("expiry_date")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return date.fromisoformat(raw[:10])
    raise ValueError(f"Cannot parse expires_at from document: {raw!r}")


def _build_subject(
    document: dict[str, Any],
    offset: int,
    expires_at: date,
) -> str:
    """Build a human-readable event subject in Russian."""
    doc_name = document.get("display_name") or document.get("name") or "Документ"
    if offset == 0:
        return f"Лоцман: {doc_name} истекает СЕГОДНЯ"
    if offset == 1:
        return f"Лоцман: {doc_name} истекает через 1 день"
    return f"Лоцман: {doc_name} истекает через {offset} дней"


def _build_body(
    document: dict[str, Any],
    offset: int,
    expires_at: date,
    web_bff_url: str,
) -> str:
    """Build a plain-text body for the calendar event."""
    doc_id = document.get("id") or document.get("document_id", "")
    doc_name = document.get("display_name") or document.get("name") or "Документ"
    asset_name = document.get("asset_name") or document.get("asset", {}).get("name", "")

    lines = [
        f"Документ: {doc_name}",
    ]
    if asset_name:
        lines.append(f"Контрагент: {asset_name}")
    lines += [
        f"Срок действия: {expires_at.isoformat()}",
        "",
        f"Ссылка: {web_bff_url}/registry/{doc_id}",
        "",
        "— Лоцман, система учёта документов",
    ]
    return "\n".join(lines)
