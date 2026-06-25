# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GET/POST/PATCH/DELETE endpoints for assets (partner companies).

Role requirements:
  - GET:    any authenticated actor
  - POST:   editor or admin (inline company creation from the document form)
  - PATCH:  admin only
  - DELETE (archive): admin only
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from registry_service.api.deps import (
    CurrentActor,
    DbSession,
    Pagination,
    RequireAdmin,
    RequireEditor,
    get_clock,
    get_request_id,
)
from registry_service.api.schemas import (
    AssetCreateRequest,
    AssetResponse,
    AssetStatusRequest,
    AssetUpdateRequest,
)
from registry_service.application.dto import (
    ChangeAssetStatusCommand,
    CreateAssetCommand,
    UpdateAssetCommand,
)
from registry_service.application.use_cases.archive_asset import ArchiveAsset
from registry_service.application.use_cases.change_asset_status import ChangeAssetStatus
from registry_service.application.use_cases.create_asset import CreateAsset
from registry_service.application.use_cases.list_assets import ListAssets
from registry_service.application.use_cases.update_asset import UpdateAsset
from registry_service.infrastructure.db.repositories import (
    SqlAssetRepository,
    SqlEventOutbox,
)

router = APIRouter(prefix="/assets", tags=["assets"])


# ---------------------------------------------------------------------------
# GET /assets
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AssetResponse])
async def list_assets(
    session: DbSession,
    actor: CurrentActor,
    pagination: Pagination,
    q: str | None = Query(default=None, description="pg_trgm name search"),
) -> list[AssetResponse]:
    repo = SqlAssetRepository(session)
    use_case = ListAssets(repo=repo)
    dtos = await use_case.execute(q=q, offset=pagination.offset, limit=pagination.limit)
    return [AssetResponse(**vars(dto)) for dto in dtos]


# ---------------------------------------------------------------------------
# POST /assets
# ---------------------------------------------------------------------------


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset(
    body: AssetCreateRequest,
    session: DbSession,
    actor: RequireEditor,
    request: Request,
) -> AssetResponse:
    # Editors (and admins) may create companies — supports inline creation
    # directly from the document-creation form (issue #5). Editing/archiving
    # remains admin-only.
    async with session.begin():
        repo = SqlAssetRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = CreateAsset(repo=repo, outbox=outbox, clock=clock)
        dto = await use_case.execute(
            cmd=CreateAssetCommand(
                name=body.name,
                inn=body.inn,
                notes=body.notes,
                actor_id=actor.actor_id,
                request_id=get_request_id(request),
            )
        )
    return AssetResponse(**vars(dto))


# ---------------------------------------------------------------------------
# PATCH /assets/{asset_id}
# ---------------------------------------------------------------------------


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdateRequest,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> AssetResponse:
    async with session.begin():
        repo = SqlAssetRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = UpdateAsset(repo=repo, outbox=outbox, clock=clock)
        dto = await use_case.execute(
            cmd=UpdateAssetCommand(
                asset_id=asset_id,
                name=body.name,
                inn=body.inn,
                notes=body.notes,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )
    return AssetResponse(**vars(dto))


# ---------------------------------------------------------------------------
# PATCH /assets/{asset_id}/status
# ---------------------------------------------------------------------------


@router.patch("/{asset_id}/status", response_model=AssetResponse, status_code=status.HTTP_200_OK)
async def change_asset_status(
    asset_id: uuid.UUID,
    body: AssetStatusRequest,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> AssetResponse:
    """Change the functional status of an asset (active | liquidating | archived).

    When setting status to 'archived', also sets deleted_at (dual-signal model)
    and cascade-archives all active documents for the asset.
    When setting status to 'active' or 'liquidating' from 'archived', clears deleted_at.
    Documents are NOT auto-restored when un-archiving — restore them individually.
    """
    async with session.begin():
        repo = SqlAssetRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = ChangeAssetStatus(repo=repo, outbox=outbox, clock=clock)
        dto, _cascaded = await use_case.execute(
            cmd=ChangeAssetStatusCommand(
                asset_id=asset_id,
                status=body.status,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )
    return AssetResponse(**vars(dto))


# ---------------------------------------------------------------------------
# PATCH /assets/{asset_id}/archive
# ---------------------------------------------------------------------------


@router.patch("/{asset_id}/archive", status_code=status.HTTP_200_OK)
async def archive_asset(
    asset_id: uuid.UUID,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> dict:
    async with session.begin():
        repo = SqlAssetRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = ArchiveAsset(repo=repo, outbox=outbox, clock=clock)
        cascaded = await use_case.execute(
            asset_id=asset_id,
            actor_id=admin.actor_id,
            request_id=get_request_id(request),
        )
    return {"cascaded_document_count": cascaded}
