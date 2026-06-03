# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""History endpoints — proxy to audit-service with registry-side enrichment.

GET /documents/{document_id}/history — used by SPA DocumentDetailDrawer's
   "История изменений" tab. Returns events flattened + asset / doc-type names
   resolved server-side. User-name resolution happens at the web-bff layer
   (which has the auth-service internal JWT key — registry-svc does not).

GET /assets/{asset_id}/history — same enrichment story for asset-scoped audit.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request

from registry_service.api.deps import CurrentActor, DbSession, get_audit_client, get_request_id
from registry_service.application.use_cases.get_asset_history import GetAssetHistory
from registry_service.application.use_cases.get_document_history import GetDocumentHistory
from registry_service.infrastructure.db.repositories import (
    SqlAssetRepository,
    SqlDocumentTypeRepository,
)

router = APIRouter(tags=["history"])


@router.get("/documents/{document_id}/history", response_model=list[dict[str, Any]])
async def get_document_history(
    document_id: uuid.UUID,
    actor: CurrentActor,
    request: Request,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    audit_client = get_audit_client(request)
    use_case = GetDocumentHistory(
        audit_client=audit_client,  # type: ignore[arg-type]
        asset_repo=SqlAssetRepository(session),
        document_type_repo=SqlDocumentTypeRepository(session),
    )
    return await use_case.execute(
        document_id=document_id,
        limit=limit,
        actor_id=actor.actor_id,
        role=actor.role,
        request_id=get_request_id(request),
    )


@router.get("/assets/{asset_id}/history", response_model=list[dict[str, Any]])
async def get_asset_history(
    asset_id: uuid.UUID,
    actor: CurrentActor,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    audit_client = get_audit_client(request)
    use_case = GetAssetHistory(audit_client=audit_client)  # type: ignore[arg-type]
    return await use_case.execute(
        asset_id=asset_id,
        limit=limit,
        actor_id=actor.actor_id,
        role=actor.role,
        request_id=get_request_id(request),
    )
