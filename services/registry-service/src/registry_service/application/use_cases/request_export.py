# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-20: Request an xlsx export job (enqueues ARQ task, returns job id)."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import ExportJobDTO, RequestExportCommand
from registry_service.application.ports import Clock, EventOutbox, ExportJobRepository
from registry_service.domain.entities import ExportJob
from registry_service.domain.events import ExportJobRequested


@dataclass(slots=True)
class RequestExport:
    repo: ExportJobRepository
    outbox: EventOutbox
    clock: Clock
    # arq_pool: Any  # ARQ job enqueueing is done in the API layer after the use case

    async def execute(self, *, cmd: RequestExportCommand) -> ExportJobDTO:
        now = self.clock.now()
        job = ExportJob.create(
            requested_by=cmd.actor_id,
            filters={
                "filters": cmd.filters,
                "visible_columns": cmd.visible_columns,
            },
            now=now,
        )

        await self.repo.add(job)

        event = ExportJobRequested(
            job_id=job.id,
            requested_by=job.requested_by,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
        await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return ExportJobDTO(
            id=job.id,
            requested_by=job.requested_by,
            status=job.status,
            file_path=job.file_path,
            error=job.error,
            expires_at=job.expires_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
