# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SQLAlchemy 2.x ORM models for the notification schema.

Tables owned by notification-service:
  - notification.delivery_attempts      (scheduled / sent / failed delivery records)
  - notification.message_templates      (per-channel, per-locale templates)
  - notification.idempotency            (provider-level idempotency keys)
  - notification.outbox                 (transactional outbox)
  - notification.outbox_dlq             (dead-letter queue)
  - notification.provider_credentials   (Fernet-encrypted channel configs — ADR-0004 §4)
  - notification.calendar_subscriptions (Exchange calendar user whitelist — ADR-0005 §3)
  - notification.calendar_event_mappings (Exchange ItemId/ChangeKey per document event — ADR-0005 §4, §9)

document_id and user_id are bare UUIDs — logical refs with no DB FK.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# notification.delivery_attempts
# ---------------------------------------------------------------------------


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'telegram', 'dion')",
            name="delivery_attempts_channel_check",
        ),
        CheckConstraint(
            "template_code IN ('pre_notice', 'in_day', 'overdue', "
            "'doc_created', 'doc_updated', 'doc_assigned', "
            "'doc_attachment', 'doc_archived', 'digest')",
            name="delivery_attempts_template_code_check",
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="delivery_attempts_status_check",
        ),
        {
            "schema": "notification",
            "comment": "Scheduled, sent, and failed notification delivery records.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    # Logical refs — no DB FK (cross-schema).
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to registry.documents(id). No DB FK.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to auth.users(id). No DB FK.",
    )
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="email | telegram | dion"
    )
    template_code: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="pre_notice | in_day | overdue"
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        comment="When this notification should be / was sent.",
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'pending'")
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Last error from provider."
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sa_text("0"),
        comment="Number of delivery attempts so far.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# notification.message_templates
# ---------------------------------------------------------------------------


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'telegram', 'dion')",
            name="message_templates_channel_check",
        ),
        {
            "schema": "notification",
            "comment": "Per-channel, per-locale notification templates.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    template_code: Mapped[str] = mapped_column(String(30), nullable=False)
    locale: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=sa_text("'ru'"),
        comment="BCP-47 locale tag, e.g. 'ru', 'en'.",
    )
    subject: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Email subject line. NULL for telegram/dion."
    )
    body_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Markdown body. Jinja2 template syntax; variables listed in variables JSONB.",
    )
    variables: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
        comment="JSON schema or list of expected template variable names.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# notification.idempotency  — provider-level deduplication
# ---------------------------------------------------------------------------


class Idempotency(Base):
    __tablename__ = "idempotency"
    __table_args__ = (
        {
            "schema": "notification",
            "comment": "Provider-level idempotency keys to prevent duplicate sends on retry.",
        },
    )

    # Composite natural PK: (provider, idempotency_key)
    provider: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
        comment="Provider name: email | telegram | dion",
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment="Unique key per outbound message attempt.",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="When this idempotency key was first recorded.",
    )
    result_payload: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=True,
        comment="Provider response payload for the first successful send.",
    )


# ---------------------------------------------------------------------------
# notification.outbox
# ---------------------------------------------------------------------------


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        {
            "schema": "notification",
            "comment": "Transactional outbox for notification domain events.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="Set by outbox-dispatcher ARQ worker after XADD to Redis Streams.",
    )
    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Redis Stream key, e.g. 'notification.deliveries'.",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# notification.outbox_dlq
# ---------------------------------------------------------------------------


class OutboxDlq(Base):
    __tablename__ = "outbox_dlq"
    __table_args__ = (
        {
            "schema": "notification",
            "comment": "Dead-letter queue for notification.outbox rows that failed all dispatch retries.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    failed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )


# ---------------------------------------------------------------------------
# notification.provider_credentials  — ADR-0004 §4
# ---------------------------------------------------------------------------


class ProviderCredentialsRow(Base):
    """One row per notification channel holding Fernet-encrypted config.

    Invariants enforced by the DB:
    - UNIQUE (channel): at most one row per channel per instance.
    - config_enc NOT NULL: real ciphertext must be present; the application is
      responsible for writing valid Fernet bytes before enabling a channel.

    The use-case / infrastructure layer owns all crypto; this model is a
    plain data-access object with no business logic.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'telegram', 'dion', 'exchange_calendar', 'ics_feed')",
            name="pc_channel_check",
        ),
        {
            "schema": "notification",
            "comment": (
                "One row per notification channel. "
                "config_enc is Fernet(CHANNEL_ENC_KEY)-encrypted JSON blob. "
                "See ADR-0004 §4, ADR-0005 §2 (exchange_calendar) and §10 (ics_feed)."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    channel: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "email | telegram | dion | exchange_calendar | ics_feed "
            "— unique per instance (pc_channel_unique)."
        ),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        comment="Whether this channel is active for delivery scheduling.",
    )
    config_enc: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment=(
            "Fernet ciphertext of JSON channel config "
            "(smtp_host/port/user/pass, bot_token, etc.). Never stored in plaintext."
        ),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the admin who last wrote this row, or SYSTEM_MIGRATOR UUID for bootstrap.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# notification.calendar_subscriptions  — ADR-0005 §3
# ---------------------------------------------------------------------------


class CalendarSubscriptionRow(Base):
    """Whitelist of users who receive Exchange calendar events from Лоцман.

    user_id is the PK and is a logical reference to auth.users(id) — no DB FK
    (cross-schema convention, same as document_id / user_id elsewhere).

    Soft-disable (enabled=false) is preferred over DELETE so that audit history
    is preserved.  The use-case layer enforces this; nothing at the DB level
    prevents a hard DELETE.

    share_status tracks the automatic EWS Reviewer permission grant lifecycle
    (ADR-0005 §7, migration 0004):
      pending       — subscription added, EWS grant in-flight or queued
      granted       — EWS permission_set succeeded
      failed        — EWS call failed; share_error contains sanitised detail
      revoked       — EWS permission_unset succeeded after subscription remove
      not_attempted — exchange_calendar channel was absent at subscribe time
    """

    __tablename__ = "calendar_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "share_status IN ('pending','granted','failed','revoked','not_attempted')",
            name="cs_share_status_check",
        ),
        {
            "schema": "notification",
            "comment": (
                "Whitelist of users who receive Exchange calendar events from Лоцман. "
                "user_id is a logical ref to auth.users(id) — no DB FK (cross-schema). "
                "Prefer enabled=false over DELETE to preserve audit history. "
                "share_status tracks automatic EWS Reviewer permission grant (ADR-0005 §7). "
                "See ADR-0005 §3."
            ),
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        comment="Logical ref to auth.users(id). No DB FK — cross-schema convention.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("true"),
        comment="false = soft-disabled (opt-out). Use this instead of DELETE.",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the admin who added this subscription.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )
    share_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sa_text("'not_attempted'"),
        comment=(
            "FSM: pending → granted | failed | not_attempted | revoked. "
            "See ADR-0005 §7 and migration 0004."
        ),
    )
    share_granted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="Set when EWS permission_set succeeded. NULL otherwise.",
    )
    share_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Last EWS error message (sanitised — no credentials). "
            "Populated on failed share grant/revoke. Cleared on success."
        ),
    )
    ics_feed_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment=(
            "Per-user ICS feed token. Public endpoint "
            "GET /api/v1/calendar/feed/{token}.ics resolves to this user's "
            "subscription. Random URL-safe string, generated on insert."
        ),
    )


# ---------------------------------------------------------------------------
# notification.calendar_event_mappings  — ADR-0005 §4, §9
# ---------------------------------------------------------------------------


class CalendarEventMappingRow(Base):
    """Tracks Exchange ItemId / ChangeKey for each calendar event published by Лоцман.

    Composite PK (document_id, notice_offset_days):
      Each document generates N calendar events — one per pre_notice_days value
      plus one due-day event (notice_offset_days=0).  See ADR-0005 §9.

    document_id is a logical reference to registry.documents(id) — no DB FK
    (cross-schema convention).

    external_marker mirrors the value written to the Exchange extended_property
    'LotsmanMarker' ("lotsman:doc:<uuid>:offset:<N>").  Storing it in both
    places enables disaster-recovery mapping reconstruction.  See ADR-0005 §13.

    change_key must be refreshed after every successful UpdateItem / DeleteItem;
    stale change_key causes EWS ErrorIrresolvableConflict.

    No relationships are declared — the use-case layer composes these objects.
    """

    __tablename__ = "calendar_event_mappings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "document_id",
            "notice_offset_days",
            name="calendar_event_mappings_pk",
        ),
        CheckConstraint(
            "sync_state IN ('pending', 'synced', 'failed', 'dlq', 'deleted')",
            name="cem_state_check",
        ),
        CheckConstraint(
            "notice_offset_days >= 0",
            name="cem_offset_check",
        ),
        {
            "schema": "notification",
            "comment": (
                "Tracks Exchange ItemId/ChangeKey for each calendar event published by Лоцман. "
                "Composite PK (document_id, notice_offset_days) because each document generates "
                "N events — one per pre_notice_days value plus one due-day event. "
                "See ADR-0005 §4 and §9."
            ),
        },
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to registry.documents(id). No DB FK — cross-schema convention.",
    )
    notice_offset_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0 = due-day event; positive = pre-notice days before expiry (e.g. 30, 14, 7, 1).",
    )
    exchange_item_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="EWS ItemId — opaque base64 string returned by Exchange on CreateItem.",
    )
    change_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "EWS ChangeKey — concurrency token required for UpdateItem / DeleteItem. "
            "Must be refreshed after each successful write."
        ),
    )
    external_marker: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment=(
            r'Copy of Exchange extended_property value: "lotsman:doc:<uuid>:offset:<N>". '
            "Stored in both DB and Exchange for disaster-recovery mapping recovery. "
            "See ADR-0005 §13."
        ),
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Timestamp of the last successful or attempted EWS sync.",
    )
    sync_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="pending | synced | failed | dlq | deleted",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last EWS error message, if any.",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sa_text("0"),
        comment=(
            "Incremented by ARQ worker on each failed sync attempt. "
            "Row moves to dlq after max attempts (application-level threshold)."
        ),
    )


# ---------------------------------------------------------------------------
# notification.user_notification_prefs — per-user preferences (ADR-0011 §D2)
# ---------------------------------------------------------------------------


class UserNotificationPref(Base):
    __tablename__ = "user_notification_prefs"
    __table_args__ = (
        CheckConstraint(
            "email_mode IN ('instant', 'digest', 'off')",
            name="user_notification_prefs_email_mode_check",
        ),
        {
            "schema": "notification",
            "comment": (
                "Per-user notification preferences (ADR-0011). user_id is a "
                "logical ref to auth.users(id), no DB FK."
            ),
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        comment="Logical ref to auth.users(id). No DB FK.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("true"),
        comment="Master switch — false silences all notifications for this user.",
    )
    suppress_own: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("true"),
        comment="Do not notify the user about actions they performed themselves.",
    )
    email_mode: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=sa_text("'digest'"),
        comment="instant | digest | off — email delivery mode for events.",
    )
    categories: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
        comment="{category: {in_app: bool, email: bool}}. Missing keys → code defaults.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# notification.user_notifications — in-app feed (ADR-0011 §D6)
# ---------------------------------------------------------------------------


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = ({"schema": "notification", "comment": "In-app notification feed."},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Recipient. Logical ref to auth.users(id). No DB FK.",
    )
    category: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="doc_created | doc_updated | ..."
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="Who caused the event."
    )
    dedup_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Idempotency key — UNIQUE per (user_id, dedup_key). Set to "
            "'{event_id}:{document_id}' (immediate) or 'upd:{document_id}:{window}' "
            "(coalesced), so redelivery does not duplicate the feed row."
        ),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    email_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        comment="True → awaiting inclusion in the next digest email.",
    )
    emailed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
