# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain events for notification-service channel management.

Event type convention: notification.<noun>.<verb>.v1

All secret fields are redacted in payloads (US-14).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from lotsman_shared.envelope import EventEnvelope, make_envelope


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Channel lifecycle events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelConfigured:
    """PUT /admin/channels/{channel} — create or full update of channel config."""

    event_type = "notification.channel.configured.v1"
    actor_id: uuid.UUID
    channel: str
    enabled: bool
    redacted_config: dict[str, Any]  # secret fields already replaced by [REDACTED]
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "channel": self.channel,
                "enabled": self.enabled,
                "config": self.redacted_config,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ChannelDisabled:
    """PATCH /admin/channels/{channel} with enabled=false."""

    event_type = "notification.channel.disabled.v1"
    actor_id: uuid.UUID
    channel: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"channel": self.channel},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ChannelTested:
    """POST /admin/channels/{channel}/test — test message dispatched."""

    event_type = "notification.channel.tested.v1"
    actor_id: uuid.UUID
    channel: str
    outcome: str  # "queued" | "failed"
    destination: str
    test_id: uuid.UUID
    error_class: str | None = None
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        payload: dict[str, Any] = {
            "channel": self.channel,
            "outcome": self.outcome,
            "destination": self.destination,
            "test_id": str(self.test_id),
        }
        if self.error_class is not None:
            payload["error_class"] = self.error_class
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload=payload,
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ChannelChanged:
    """Internal event to trigger hot-reload in notification-service.

    This event has no human actor — it is emitted by the system when
    any channel config changes and notification-service should re-init its
    provider clients (US-7 / ADR-0004 §4).

    consumer wiring (hot-reload reactor) is Phase 4 / follow-up.
    """

    event_type = "notification.channel.changed.v1"
    system_actor_id: uuid.UUID
    channel: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.system_actor_id,
            payload={"channel": self.channel},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ChannelRekeyed:
    """notification.channel.rekeyed.v1 — all rows re-encrypted with new key."""

    event_type = "notification.channel.rekeyed.v1"
    system_actor_id: uuid.UUID
    count: int
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.system_actor_id,
            payload={"count": self.count},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Calendar sync events (ADR-0005 §5)
# ---------------------------------------------------------------------------

_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000002")


# ---------------------------------------------------------------------------
# Calendar subscription share events (ADR-0005 §7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarShareGranted:
    """notification.calendar.share_granted.v1 — EWS permission set succeeded."""

    event_type = "notification.calendar.share_granted.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "user_email": self.user_email,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class CalendarShareFailed:
    """notification.calendar.share_failed.v1 — EWS permission set failed."""

    event_type = "notification.calendar.share_failed.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    error_class: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "error_class": self.error_class,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class CalendarShareNotAttempted:
    """notification.calendar.share_not_attempted.v1 — channel not configured."""

    event_type = "notification.calendar.share_not_attempted.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class CalendarShareRevoked:
    """notification.calendar.share_revoked.v1 — EWS permission removed."""

    event_type = "notification.calendar.share_revoked.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "user_email": self.user_email,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Calendar sync events (ADR-0005 §5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarSyncSucceeded:
    """notification.calendar.sync_succeeded.v1."""

    event_type = "notification.calendar.sync_succeeded.v1"
    document_id: uuid.UUID
    offsets_synced: list[int]
    offsets_deleted: list[int]
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=_SYSTEM_ACTOR,
            payload={
                "document_id": str(self.document_id),
                "offsets_synced": self.offsets_synced,
                "offsets_deleted": self.offsets_deleted,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class CalendarSyncFailed:
    """notification.calendar.sync_failed.v1."""

    event_type = "notification.calendar.sync_failed.v1"
    document_id: uuid.UUID
    offsets_failed: list[int]
    offsets_synced: list[int]
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=_SYSTEM_ACTOR,
            payload={
                "document_id": str(self.document_id),
                "offsets_failed": self.offsets_failed,
                "offsets_synced": self.offsets_synced,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )
