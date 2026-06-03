# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Calendar domain models.

Pydantic value objects used by CalendarDriver implementations and
SyncCalendarEvent use case.  Domain layer — no infrastructure imports.

See ADR-0005 §4, §6, §9, §13.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Input / output types for CalendarDriver
# ---------------------------------------------------------------------------


class CalendarMapping(BaseModel):
    """Existing DB mapping row for a (document_id, notice_offset_days) pair.

    Wraps the data the driver needs to perform an UpdateItem / DeleteItem on
    an existing Exchange event.  exchange_item_id + change_key are EWS
    concurrency tokens that MUST be kept fresh after every successful write.
    """

    document_id: uuid.UUID
    notice_offset_days: int = Field(ge=0)
    exchange_item_id: str
    change_key: str
    external_marker: str
    sync_state: str
    retry_count: int = 0


class CalendarEventData(BaseModel):
    """Data required to create or update an Exchange calendar event.

    All-day events only (ADR-0005 §3).  subject is pre-rendered by the use
    case so the driver stays format-agnostic.
    """

    document_id: uuid.UUID
    notice_offset_days: int = Field(ge=0)
    event_date: date
    """Calendar day on which this event sits (expires_at - notice_offset_days)."""

    subject: str
    """Pre-rendered, e.g. «Лоцман: Лицензия истекает через 14 дней»."""

    body: str
    """Plain-text body (not HTML) with link to document."""

    external_marker: str
    """Stable identifier: "lotsman:doc:<uuid>:offset:<N>".  Written as
    Exchange ExtendedProperty for disaster-recovery.  See ADR-0005 §13."""


class CalendarSyncResult(BaseModel):
    """Outcome of a successful upsert_event() call."""

    exchange_item_id: str
    change_key: str
    external_marker: str
    was_created: bool
    """True = CreateItem; False = UpdateItem."""


class OrphanEvent(BaseModel):
    """An Exchange item found via ExtendedProperty scan that has no matching DB row."""

    exchange_item_id: str
    change_key: str
    external_marker: str
    """Parsed from ExtendedProperty; may be a lotsman marker or garbage."""


class CalendarTestResult(BaseModel):
    """Result of CalendarDriver.test_connection()."""

    success: bool
    detail: str
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Sync decision types
# ---------------------------------------------------------------------------

SyncAction = Literal["create", "update", "delete", "noop"]


class MappingSyncDecision(BaseModel):
    """Single (document_id, notice_offset_days) sync decision."""

    document_id: uuid.UUID
    notice_offset_days: int = Field(ge=0)
    action: SyncAction
    mapping: CalendarMapping | None = None
    """Existing mapping row if action is update or delete; None for create."""

    event_data: CalendarEventData | None = None
    """Populated for create / update actions; None for delete."""
