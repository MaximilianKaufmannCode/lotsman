# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Q8: ARQ cron task — purge export files older than 24 hours.

Runs hourly. Sets purged_at on the job row and deletes the file from disk.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER

from registry_service.application.ports import (
    Clock,
    EventOutbox,
    ExportJobRepository,
    ExportStorage,
)
from registry_service.domain.events import ExportJobPurged

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PurgeExpiredExports:
    repo: ExportJobRepository
    storage: ExportStorage
    outbox: EventOutbox
    clock: Clock

    async def execute(self) -> int:
        """Delete all expired, unpurged export files.

        Returns:
            Number of jobs purged.
        """
        jobs = await self.repo.list_expired_not_purged()
        purged = 0

        for job in jobs:
            try:
                if job.file_path:
                    await self.storage.delete(job.file_path)

                now = self.clock.now()
                # Mark as purged by clearing file_path (storage layer deleted the file)
                job.file_path = None
                job.updated_at = now
                await self.repo.update(job)

                event = ExportJobPurged(
                    job_id=job.id,
                    actor_id=ACTOR_OUTBOX_DISPATCHER,  # system actor
                    occurred_at=now,
                )
                await self.outbox.publish(event.as_envelope(), topic=event.topic)

                purged += 1
                log.info("export_purged", job_id=str(job.id))
            except Exception:
                log.exception("export_purge_failed", job_id=str(job.id))

        return purged
