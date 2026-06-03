# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Export job endpoints (US-20).

POST /exports      — request xlsx export (enqueues ARQ job)
GET  /exports/{id} — poll job status
GET  /exports/{id}/download — redirect to signed URL
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Request, status
from fastapi.responses import RedirectResponse

from registry_service.api.deps import (
    CurrentActor,
    DbSession,
    get_clock,
    get_export_storage,
    get_request_id,
)
from registry_service.api.schemas import ExportJobResponse, ExportRequestBody
from registry_service.application.dto import RequestExportCommand
from registry_service.application.use_cases.download_export import DownloadExport
from registry_service.application.use_cases.request_export import RequestExport
from registry_service.infrastructure.db.repositories import (
    SqlEventOutbox,
    SqlExportJobRepository,
)

router = APIRouter(prefix="/exports", tags=["exports"])


# ---------------------------------------------------------------------------
# POST /exports
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def request_export(
    body: ExportRequestBody,
    session: DbSession,
    actor: CurrentActor,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    async with session.begin():
        repo = SqlExportJobRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = RequestExport(repo=repo, outbox=outbox, clock=clock)
        dto = await use_case.execute(
            cmd=RequestExportCommand(
                filters=body.filters,
                visible_columns=body.visible_columns,
                actor_id=actor.actor_id,
                request_id=get_request_id(request),
            )
        )

    # Enqueue ARQ task after transaction commits
    # In production the ARQ pool lives on app.state; here we use a background task
    # as the trigger. The ARQ worker picks up the job from the DB.
    job_id = str(dto.id)
    try:
        arq_pool = getattr(request.app.state, "arq_pool", None)
        if arq_pool:
            await arq_pool.enqueue_job("run_export_job", job_id=job_id)
    except Exception:
        pass  # ARQ enqueue failure: the job row exists; the worker will pick it up on next poll

    return {"job_id": dto.id, "status": dto.status}


# ---------------------------------------------------------------------------
# GET /exports/{job_id}
# ---------------------------------------------------------------------------


@router.get("/{job_id}", response_model=ExportJobResponse)
async def get_export_job(
    job_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    request: Request,
) -> ExportJobResponse:
    repo = SqlExportJobRepository(session)
    storage = get_export_storage(request.app.state.settings)  # type: ignore[arg-type]
    clock = get_clock()
    use_case = DownloadExport(repo=repo, storage=storage, clock=clock)  # type: ignore[arg-type]
    dto = await use_case.get_job(job_id=job_id)
    return ExportJobResponse(
        job_id=dto.id,
        status=dto.status,
        file_path=dto.file_path,
        error=dto.error,
        expires_at=dto.expires_at,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /exports/{job_id}/download
# ---------------------------------------------------------------------------


@router.get("/{job_id}/download")
async def download_export(
    job_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    request: Request,
) -> RedirectResponse:
    repo = SqlExportJobRepository(session)
    storage = get_export_storage(request.app.state.settings)  # type: ignore[arg-type]
    clock = get_clock()
    use_case = DownloadExport(repo=repo, storage=storage, clock=clock)  # type: ignore[arg-type]
    signed = await use_case.get_download_url(job_id=job_id)
    return RedirectResponse(url=signed.url, status_code=302)
