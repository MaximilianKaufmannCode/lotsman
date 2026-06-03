# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SQLAlchemy repository implementations for notification-service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from lotsman_shared.envelope import EventEnvelope
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.db.models import (
    CalendarEventMappingRow,
    CalendarSubscriptionRow,
    DeliveryAttempt,
    Outbox,
    ProviderCredentialsRow,
    UserNotification,
    UserNotificationPref,
)
from notification_service.domain.calendar import CalendarMapping

# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


class SqlaEventOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(self, envelope: EventEnvelope) -> None:
        # Topic convention (matches auth-svc/registry-svc): 2-segment
        # `<service>.<aggregate>` derived from envelope.type's first 2 segments.
        # Examples:
        #   envelope.type="notification.calendar.sync_succeeded.v1"
        #     → topic="notification.calendar"
        #   envelope.type="notification.channel.changed.v1"
        #     → topic="notification.channel"
        #   envelope.type="notification.email.reminder_sent.v1"
        #     → topic="notification.email"
        # FIXED 2026-05-25: previously was `f"notification.{envelope.type}"` which
        # produced double-prefix `notification.notification.*` (~70 stuck events
        # in notification.outbox with topic of that shape; audit consumer
        # subscribed to `notification.deliveries` only, so events never reached
        # audit.events). After this fix, new events use the proper 2-segment
        # topic and are pickable by audit consumer once subscription list is
        # extended (audit-service/.../consumer/recorder.py).
        parts = envelope.type.split(".")
        topic = ".".join(parts[:2]) if len(parts) >= 2 else envelope.type
        row = Outbox(
            topic=topic,
            payload=envelope.model_dump(mode="json"),
        )
        self._session.add(row)
        await self._session.flush()


# ---------------------------------------------------------------------------
# CredentialRepository
# ---------------------------------------------------------------------------


class SqlaCredentialRepository:
    """Implements CredentialRepository protocol using SQLAlchemy async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[Any]:
        result = await self._session.execute(select(ProviderCredentialsRow))
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        channel: str,
        enabled: bool,
        config_enc: bytes,
        actor_id: uuid.UUID,
    ) -> None:
        """INSERT … ON CONFLICT (channel) DO UPDATE.

        created_by is only set on INSERT.
        updated_at is managed by a DB trigger.
        """
        stmt = pg_insert(ProviderCredentialsRow).values(
            channel=channel,
            enabled=enabled,
            config_enc=config_enc,
            created_by=actor_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["channel"],
            set_={
                "enabled": stmt.excluded.enabled,
                "config_enc": stmt.excluded.config_enc,
                # created_by intentionally NOT updated — preserves original creator.
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def set_enabled(self, *, channel: str, enabled: bool) -> None:
        await self._session.execute(
            text(
                "UPDATE notification.provider_credentials "
                "SET enabled = :enabled "
                "WHERE channel = :channel"
            ),
            {"enabled": enabled, "channel": channel},
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# CalendarEventMappingRepository
# ---------------------------------------------------------------------------


class SqlaCalendarEventMappingRepository:
    """Implements CalendarEventMappingRepository using SQLAlchemy async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_document(self, document_id: uuid.UUID) -> list[CalendarMapping]:
        result = await self._session.execute(
            select(CalendarEventMappingRow).where(
                CalendarEventMappingRow.document_id == document_id
            )
        )
        rows = result.scalars().all()
        return [
            CalendarMapping(
                document_id=row.document_id,
                notice_offset_days=row.notice_offset_days,
                exchange_item_id=row.exchange_item_id,
                change_key=row.change_key,
                external_marker=row.external_marker,
                sync_state=row.sync_state,
                retry_count=row.retry_count,
            )
            for row in rows
        ]

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
    ) -> None:
        stmt = pg_insert(CalendarEventMappingRow).values(
            document_id=document_id,
            notice_offset_days=notice_offset_days,
            exchange_item_id=exchange_item_id,
            change_key=change_key,
            external_marker=external_marker,
            sync_state=sync_state,
            last_error=last_error,
            retry_count=retry_count,
            last_synced_at=datetime.now(tz=UTC),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["document_id", "notice_offset_days"],
            set_={
                "exchange_item_id": stmt.excluded.exchange_item_id,
                "change_key": stmt.excluded.change_key,
                "external_marker": stmt.excluded.external_marker,
                "sync_state": stmt.excluded.sync_state,
                "last_error": stmt.excluded.last_error,
                "retry_count": stmt.excluded.retry_count,
                "last_synced_at": stmt.excluded.last_synced_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete(
        self, *, document_id: uuid.UUID, notice_offset_days: int
    ) -> None:
        await self._session.execute(
            text(
                "DELETE FROM notification.calendar_event_mappings "
                "WHERE document_id = :doc_id AND notice_offset_days = :offset"
            ),
            {"doc_id": document_id, "offset": notice_offset_days},
        )
        await self._session.flush()

    async def list_stale(
        self,
        *,
        states: list[str],
        older_than_minutes: int,
        limit: int,
    ) -> list[CalendarMapping]:
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=older_than_minutes)
        result = await self._session.execute(
            select(CalendarEventMappingRow)
            .where(
                CalendarEventMappingRow.sync_state.in_(states),
                CalendarEventMappingRow.last_synced_at < cutoff,
            )
            .limit(limit)
        )
        rows = result.scalars().all()
        return [
            CalendarMapping(
                document_id=row.document_id,
                notice_offset_days=row.notice_offset_days,
                exchange_item_id=row.exchange_item_id,
                change_key=row.change_key,
                external_marker=row.external_marker,
                sync_state=row.sync_state,
                retry_count=row.retry_count,
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# CalendarSubscriptionRepository
# ---------------------------------------------------------------------------


class SqlaCalendarSubscriptionRepository:
    """Implements CalendarSubscriptionRepository using SQLAlchemy async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Any]:
        result = await self._session.execute(select(CalendarSubscriptionRow))
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID) -> Any | None:
        result = await self._session.execute(
            select(CalendarSubscriptionRow).where(
                CalendarSubscriptionRow.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        enabled: bool,
        created_by: uuid.UUID,
        share_status: str = "not_attempted",
    ) -> None:
        # Generate a fresh ICS-feed token on insert (URL-safe, 32 chars).
        # ON CONFLICT preserves the existing token — re-enabling a subscription
        # must not invalidate the user's existing Outlook subscription URL.
        import secrets

        new_token = secrets.token_urlsafe(24)
        stmt = pg_insert(CalendarSubscriptionRow).values(
            user_id=user_id,
            enabled=enabled,
            created_by=created_by,
            share_status=share_status,
            ics_feed_token=new_token,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "enabled": stmt.excluded.enabled,
                "share_status": stmt.excluded.share_status,
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_by_ics_token(self, token: str) -> Any | None:
        result = await self._session.execute(
            select(CalendarSubscriptionRow).where(
                CalendarSubscriptionRow.ics_feed_token == token
            )
        )
        return result.scalar_one_or_none()

    async def set_share_status(
        self,
        *,
        user_id: uuid.UUID,
        share_status: str,
        share_granted_at: datetime | None = None,
        share_error: str | None = None,
    ) -> None:
        """Update only the share_status + optional share_granted_at / share_error fields."""
        sets = ["share_status = :share_status", "share_error = :share_error"]
        params: dict[str, Any] = {
            "user_id": user_id,
            "share_status": share_status,
            "share_error": share_error,
        }
        if share_granted_at is not None:
            sets.append("share_granted_at = :share_granted_at")
            params["share_granted_at"] = share_granted_at
        await self._session.execute(
            text(
                "UPDATE notification.calendar_subscriptions SET "
                + ", ".join(sets)
                + " WHERE user_id = :user_id"
            ),
            params,
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# UserNotificationPrefRepository  (ADR-0011 §D2)
# ---------------------------------------------------------------------------


class SqlaUserNotificationPrefRepository:
    """Read/write per-user notification preferences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> UserNotificationPref | None:
        result = await self._session.execute(
            select(UserNotificationPref).where(
                UserNotificationPref.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[UserNotificationPref]:
        result = await self._session.execute(select(UserNotificationPref))
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        enabled: bool,
        suppress_own: bool,
        email_mode: str,
        categories: dict[str, dict[str, bool]],
    ) -> UserNotificationPref:
        stmt = pg_insert(UserNotificationPref).values(
            user_id=user_id,
            enabled=enabled,
            suppress_own=suppress_own,
            email_mode=email_mode,
            categories=categories,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "enabled": stmt.excluded.enabled,
                "suppress_own": stmt.excluded.suppress_own,
                "email_mode": stmt.excluded.email_mode,
                "categories": stmt.excluded.categories,
                "updated_at": text("now()"),
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self.get(user_id)
        assert row is not None  # just upserted
        return row


# ---------------------------------------------------------------------------
# UserNotificationRepository  (in-app feed, ADR-0011 §D6)
# ---------------------------------------------------------------------------


class SqlaUserNotificationRepository:
    """Read/write the in-app notification feed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        category: str,
        title: str,
        body: str,
        document_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
        email_pending: bool,
        dedup_key: str | None = None,
    ) -> bool:
        """Insert a feed row. Returns True if inserted, False if a duplicate was
        suppressed via the (user_id, dedup_key) unique index (C1 idempotency).
        """
        if dedup_key is None:
            self._session.add(
                UserNotification(
                    user_id=user_id,
                    category=category,
                    title=title,
                    body=body,
                    document_id=document_id,
                    actor_id=actor_id,
                    email_pending=email_pending,
                )
            )
            await self._session.flush()
            return True

        stmt = (
            pg_insert(UserNotification)
            .values(
                user_id=user_id,
                category=category,
                title=title,
                body=body,
                document_id=document_id,
                actor_id=actor_id,
                email_pending=email_pending,
                dedup_key=dedup_key,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "dedup_key"],
                index_where=text("dedup_key IS NOT NULL"),
            )
            .returning(UserNotification.id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def record_delivery_attempt(
        self,
        *,
        document_id: uuid.UUID | None,
        user_id: uuid.UUID,
        template_code: str,
        status: str,
        error: str | None,
    ) -> None:
        """Record an event-email send in delivery_attempts (C3 observability).

        Only used for per-document event emails (document_id required by schema).
        """
        if document_id is None:
            return
        self._session.add(
            DeliveryAttempt(
                document_id=document_id,
                user_id=user_id,
                channel="email",
                template_code=template_code,
                scheduled_at=datetime.now(tz=UTC),
                sent_at=datetime.now(tz=UTC) if status == "sent" else None,
                status=status,
                error=(error or None) if status != "sent" else None,
            )
        )
        await self._session.flush()

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[UserNotification]:
        result = await self._session.execute(
            select(UserNotification)
            .where(UserNotification.user_id == user_id)
            .order_by(UserNotification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_unread(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(UserNotification)
            .where(UserNotification.user_id == user_id, UserNotification.is_read.is_(False))
        )
        return int(result.scalar_one())

    async def mark_read(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            text(
                "UPDATE notification.user_notifications SET is_read = TRUE "
                "WHERE id = :nid AND user_id = :uid AND is_read = FALSE"
            ),
            {"nid": notification_id, "uid": user_id},
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            text(
                "UPDATE notification.user_notifications SET is_read = TRUE "
                "WHERE user_id = :uid AND is_read = FALSE"
            ),
            {"uid": user_id},
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_email_pending(self, *, limit: int = 500) -> list[UserNotification]:
        result = await self._session.execute(
            select(UserNotification)
            .where(UserNotification.email_pending.is_(True))
            .order_by(UserNotification.user_id, UserNotification.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_emailed(self, ids: list[uuid.UUID]) -> None:
        if not ids:
            return
        await self._session.execute(
            text(
                "UPDATE notification.user_notifications "
                "SET email_pending = FALSE, emailed_at = now() "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        await self._session.flush()
