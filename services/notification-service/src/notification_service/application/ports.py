# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Repository and outbox port protocols for notification-service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from lotsman_shared.envelope import EventEnvelope

from notification_service.domain.calendar import (
    CalendarEventData,
    CalendarMapping,
    CalendarSyncResult,
    CalendarTestResult,
    OrphanEvent,
)


class DeliveryAttemptRepository(Protocol):
    async def get_by_id(self, attempt_id: uuid.UUID) -> object | None: ...
    async def add(self, attempt: object) -> None: ...
    async def update(self, attempt: object) -> None: ...
    async def list_pending(self) -> list[object]: ...


class MessageTemplateRepository(Protocol):
    async def get(self, channel: str, template_code: str, locale: str) -> object | None: ...
    async def list_all(self) -> list[object]: ...


class EventOutbox(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None: ...


# ---------------------------------------------------------------------------
# Channel credential port (ADR-0004 §4)
# ---------------------------------------------------------------------------


class ChannelCredentialRow(Protocol):
    """Read-model returned by CredentialRepository.get_all()."""

    id: uuid.UUID
    channel: str
    enabled: bool
    config_enc: bytes
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CredentialRepository(Protocol):
    """Port for notification.provider_credentials persistence."""

    async def get_all(self) -> list[Any]: ...

    async def upsert(
        self,
        *,
        channel: str,
        enabled: bool,
        config_enc: bytes,
        actor_id: uuid.UUID,
    ) -> None:
        """INSERT … ON CONFLICT (channel) DO UPDATE.

        created_by is only set on INSERT; updated_at is trigger-managed.
        """
        ...

    async def set_enabled(self, *, channel: str, enabled: bool) -> None:
        """Update only the enabled flag, leave config_enc untouched."""
        ...


class RedisInviteStore(Protocol):
    """SCAN invite:otp:* keys — used by DisableChannel pre-check."""

    async def has_pending_invites(self) -> bool: ...


# ---------------------------------------------------------------------------
# Calendar ports (ADR-0005 §6)
# ---------------------------------------------------------------------------


class CalendarDriver(Protocol):
    """Abstract driver for calendar event synchronisation.

    Concrete implementations: EwsCalendarDriver (exchangelib).
    Future: MsGraphCalendarDriver (ADR-0007).

    All methods run within asyncio.to_thread() if the underlying library is
    synchronous (exchangelib is sync under the hood).
    """

    async def upsert_event(
        self,
        *,
        mailbox: str,
        mapping: CalendarMapping | None,
        event_data: CalendarEventData,
    ) -> CalendarSyncResult:
        """Create or update a single calendar event.

        If mapping is None → CreateItem.
        If mapping is provided → UpdateItem using its change_key.
        Returns fresh exchange_item_id + change_key.
        """
        ...

    async def delete_event(self, *, mailbox: str, mapping: CalendarMapping) -> None:
        """Delete an existing calendar event.

        Uses mapping.exchange_item_id + mapping.change_key for EWS concurrency.
        """
        ...

    async def find_orphans(self, *, mailbox: str) -> list[OrphanEvent]:
        """Scan the mailbox calendar for events with our ExtendedProperty marker.

        Used by RecoverMappingsFromExchange (ADR-0005 §13) to rebuild the DB
        mapping table after a disaster scenario.  Returns all events that carry
        our PropertySetId, regardless of whether a DB row exists.
        """
        ...

    async def test_connection(self, *, mailbox: str) -> CalendarTestResult:
        """Probe EWS connectivity by creating then immediately deleting a probe event.

        Returns CalendarTestResult.success=True if both operations succeed.
        """
        ...

    async def upsert_heartbeat(self, *, mailbox: str) -> None:
        """Create or update the daily heartbeat event (ADR-0005 §11).

        Fixed external_marker='lotsman:heartbeat'.  Subject is 'Лоцман: Sync OK'.
        Idempotent — safe to call multiple times per day.
        """
        ...


class CalendarEventMappingRepository(Protocol):
    """Port for notification.calendar_event_mappings persistence."""

    async def get_by_document(
        self, document_id: uuid.UUID
    ) -> list[CalendarMapping]: ...

    async def upsert(
        self,
        *,
        document_id: uuid.UUID,
        notice_offset_days: int,
        exchange_item_id: str,
        change_key: str,
        external_marker: str,
        sync_state: str,
        last_error: str | None,
        retry_count: int,
    ) -> None: ...

    async def delete(
        self, *, document_id: uuid.UUID, notice_offset_days: int
    ) -> None: ...

    async def list_stale(
        self, *, states: list[str], older_than_minutes: int, limit: int
    ) -> list[CalendarMapping]: ...


class CalendarSubscriptionRepository(Protocol):
    """Port for notification.calendar_subscriptions persistence."""

    async def list_all(self) -> list[Any]: ...

    async def get(self, user_id: uuid.UUID) -> Any | None: ...

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        enabled: bool,
        created_by: uuid.UUID,
        share_status: str = "not_attempted",
    ) -> None: ...

    async def set_share_status(
        self,
        *,
        user_id: uuid.UUID,
        share_status: str,
        share_granted_at: datetime | None = None,
        share_error: str | None = None,
    ) -> None: ...


class RegistryDocumentGateway(Protocol):
    """Port for fetching document + document-type data from registry-service.

    Implemented by an httpx-based adapter that calls registry-service via
    internal JWT.  The use case imports only this Protocol.
    """

    async def get_document(self, document_id: uuid.UUID) -> dict[str, Any] | None:
        """Return document dict or None if 404."""
        ...

    async def get_document_type(self, type_code: str) -> dict[str, Any] | None:
        """Return document-type dict (including pre_notice_days list) or None."""
        ...

    async def list_active_documents(self) -> list[dict[str, Any]]:
        """Return all active (non-archived) documents that have expires_at set.

        Used by daily reconciliation and ICS feed generation.
        """
        ...
