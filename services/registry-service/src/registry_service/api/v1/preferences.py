# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tenant-wide UI preferences (column order, future visibility defaults).

Endpoints:
  GET  /api/v1/preferences/column-order        — read (any authenticated user)
  PUT  /api/v1/admin/preferences/column-order  — write (admin only)

Storage: registry.tenant_preferences (key/value JSONB). One row per setting.
Audit trail flows through the existing registry outbox via the
RegistryColumnOrderChanged event so that audit-svc records who changed what.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from lotsman_shared.envelope import make_envelope

from registry_service.api.deps import CurrentActor, DbSession, RequireAdmin
from registry_service.infrastructure.db.repositories import SqlEventOutbox

router = APIRouter(tags=["preferences"])

_KEY_COLUMN_ORDER = "registry.column_order"
_KEY_COLUMN_LABELS = "registry.column_labels"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ColumnOrderResponse(BaseModel):
    order: list[str]
    pinned_column_id: str | None = None
    updated_at: datetime | None = None


class ColumnOrderUpdateRequest(BaseModel):
    order: list[str] = Field(min_length=1, max_length=100)
    pinned_column_id: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# GET /preferences/column-order  (read)
# ---------------------------------------------------------------------------


@router.get("/preferences/column-order", response_model=ColumnOrderResponse)
async def get_column_order(
    session: DbSession,
    _actor: CurrentActor,
) -> ColumnOrderResponse:
    async with session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT value, updated_at FROM registry.tenant_preferences "
                    "WHERE key = :k"
                ),
                {"k": _KEY_COLUMN_ORDER},
            )
        ).first()

    if row is None:
        return ColumnOrderResponse(order=[], pinned_column_id=None, updated_at=None)

    value: dict[str, Any] = row.value if isinstance(row.value, dict) else {}
    order_raw = value.get("order")
    order: list[str] = (
        [str(x) for x in order_raw] if isinstance(order_raw, list) else []
    )
    pinned = value.get("pinned_column_id")
    return ColumnOrderResponse(
        order=order,
        pinned_column_id=str(pinned) if pinned else None,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# PUT /admin/preferences/column-order  (admin write)
# ---------------------------------------------------------------------------


@router.put(
    "/admin/preferences/column-order",
    response_model=ColumnOrderResponse,
    status_code=status.HTTP_200_OK,
)
async def update_column_order(
    body: ColumnOrderUpdateRequest,
    session: DbSession,
    admin: RequireAdmin,
) -> ColumnOrderResponse:
    actor_id = admin.actor_id

    # Deduplicate while preserving order; reject empty entries.
    seen: set[str] = set()
    cleaned: list[str] = []
    for col_id in body.order:
        cid = col_id.strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        cleaned.append(cid)

    pinned_id = (
        body.pinned_column_id.strip() if body.pinned_column_id else None
    ) or None
    # Invariant: pinned column must exist in the order list and sit at index 0
    # (display contract — pinned column is the leftmost data column).
    if pinned_id and pinned_id in cleaned:
        cleaned = [pinned_id] + [c for c in cleaned if c != pinned_id]

    payload: dict[str, Any] = {"order": cleaned, "pinned_column_id": pinned_id}

    async with session.begin():
        await session.execute(
            text(
                """
                INSERT INTO registry.tenant_preferences (key, value, updated_by)
                VALUES (:k, :v, :u)
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                """
            ),
            {"k": _KEY_COLUMN_ORDER, "v": __import__("json").dumps(payload), "u": actor_id},
        )

        outbox = SqlEventOutbox(session)
        envelope = make_envelope(
            event_type="registry.preferences.column_order_changed.v1",
            actor_id=actor_id,
            payload={"order": cleaned, "pinned_column_id": pinned_id},
        )
        await outbox.publish(envelope, topic="registry.preferences")

        row = (
            await session.execute(
                text(
                    "SELECT updated_at FROM registry.tenant_preferences WHERE key = :k"
                ),
                {"k": _KEY_COLUMN_ORDER},
            )
        ).first()

    return ColumnOrderResponse(
        order=cleaned,
        pinned_column_id=pinned_id,
        updated_at=row.updated_at if row else None,
    )


# ---------------------------------------------------------------------------
# Column-labels (per-tenant rename of any column id)
# ---------------------------------------------------------------------------


class ColumnLabelsResponse(BaseModel):
    labels: dict[str, str]
    updated_at: datetime | None = None


class ColumnLabelsUpdateRequest(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict, max_length=200)


@router.get("/preferences/column-labels", response_model=ColumnLabelsResponse)
async def get_column_labels(
    session: DbSession,
    _actor: CurrentActor,
) -> ColumnLabelsResponse:
    async with session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT value, updated_at FROM registry.tenant_preferences "
                    "WHERE key = :k"
                ),
                {"k": _KEY_COLUMN_LABELS},
            )
        ).first()
    if row is None:
        return ColumnLabelsResponse(labels={}, updated_at=None)
    value: dict[str, Any] = row.value if isinstance(row.value, dict) else {}
    raw = value.get("labels", {})
    labels: dict[str, str] = (
        {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    )
    return ColumnLabelsResponse(labels=labels, updated_at=row.updated_at)


@router.put(
    "/admin/preferences/column-labels",
    response_model=ColumnLabelsResponse,
    status_code=status.HTTP_200_OK,
)
async def update_column_labels(
    body: ColumnLabelsUpdateRequest,
    session: DbSession,
    admin: RequireAdmin,
) -> ColumnLabelsResponse:
    actor_id = admin.actor_id

    cleaned: dict[str, str] = {}
    for k, v in body.labels.items():
        col_id = str(k).strip()
        label = str(v).strip()
        if not col_id:
            continue
        # Empty / whitespace-only label = «remove override».
        if not label:
            continue
        if len(label) > 100:
            label = label[:100]
        cleaned[col_id] = label

    payload: dict[str, Any] = {"labels": cleaned}

    async with session.begin():
        await session.execute(
            text(
                """
                INSERT INTO registry.tenant_preferences (key, value, updated_by)
                VALUES (:k, :v, :u)
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                """
            ),
            {"k": _KEY_COLUMN_LABELS, "v": __import__("json").dumps(payload), "u": actor_id},
        )

        outbox = SqlEventOutbox(session)
        envelope = make_envelope(
            event_type="registry.preferences.column_labels_changed.v1",
            actor_id=actor_id,
            payload={"labels": cleaned},
        )
        await outbox.publish(envelope, topic="registry.preferences")

        row = (
            await session.execute(
                text(
                    "SELECT updated_at FROM registry.tenant_preferences WHERE key = :k"
                ),
                {"k": _KEY_COLUMN_LABELS},
            )
        ).first()

    return ColumnLabelsResponse(
        labels=cleaned,
        updated_at=row.updated_at if row else None,
    )
