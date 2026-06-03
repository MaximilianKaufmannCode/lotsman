# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ListDeliveryHistory use case — read-only history of email/telegram/dion sends.

Returns paginated delivery_attempts rows with optional filters. Admin UI calls
this via BFF proxy. No re-MFA needed (read-only).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.db.models import DeliveryAttempt

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ListDeliveryHistoryQuery:
    limit: int = 50  # max page size
    offset: int = 0
    status: str | None = None          # 'pending' | 'sent' | 'failed'
    template_code: str | None = None   # 'pre_notice' | 'in_day' | 'overdue'
    channel: str | None = None         # 'email' | 'telegram' | 'dion'
    document_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(slots=True)
class ListDeliveryHistory:
    session: AsyncSession

    async def execute(self, *, query: ListDeliveryHistoryQuery) -> dict[str, Any]:
        # Cap pagination to avoid runaway scans
        limit = max(1, min(query.limit, 200))
        offset = max(0, query.offset)

        session = self.session
        stmt = select(DeliveryAttempt)
        count_stmt = select(func.count()).select_from(DeliveryAttempt)

        if query.status:
            stmt = stmt.where(DeliveryAttempt.status == query.status)
            count_stmt = count_stmt.where(DeliveryAttempt.status == query.status)
        if query.template_code:
            stmt = stmt.where(DeliveryAttempt.template_code == query.template_code)
            count_stmt = count_stmt.where(
                DeliveryAttempt.template_code == query.template_code
            )
        if query.channel:
            stmt = stmt.where(DeliveryAttempt.channel == query.channel)
            count_stmt = count_stmt.where(DeliveryAttempt.channel == query.channel)
        if query.document_id:
            stmt = stmt.where(DeliveryAttempt.document_id == query.document_id)
            count_stmt = count_stmt.where(
                DeliveryAttempt.document_id == query.document_id
            )
        if query.user_id:
            stmt = stmt.where(DeliveryAttempt.user_id == query.user_id)
            count_stmt = count_stmt.where(DeliveryAttempt.user_id == query.user_id)
        if query.date_from:
            df = datetime.combine(query.date_from, datetime.min.time())
            stmt = stmt.where(DeliveryAttempt.scheduled_at >= df)
            count_stmt = count_stmt.where(DeliveryAttempt.scheduled_at >= df)
        if query.date_to:
            dt = datetime.combine(query.date_to, datetime.max.time())
            stmt = stmt.where(DeliveryAttempt.scheduled_at <= dt)
            count_stmt = count_stmt.where(DeliveryAttempt.scheduled_at <= dt)

        stmt = (
            stmt.order_by(desc(DeliveryAttempt.created_at)).limit(limit).offset(offset)
        )

        rows = (await session.execute(stmt)).scalars().all()
        total = (await session.execute(count_stmt)).scalar_one()

        items = [
            {
                "id": str(r.id),
                "document_id": str(r.document_id),
                "user_id": str(r.user_id),
                "channel": r.channel,
                "template_code": r.template_code,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "status": r.status,
                "error": r.error,
                "retry_count": r.retry_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return {"items": items, "total": int(total), "limit": limit, "offset": offset}
