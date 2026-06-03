# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-23: Bulk soft-delete up to 100 documents in one operation."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import BulkArchiveCommand, BulkArchiveResult
from registry_service.application.policies.bulk_policy import validate_bulk_count
from registry_service.application.ports import Clock, DocumentRepository, EventOutbox
from registry_service.domain.events import DocumentBulkArchived


@dataclass(slots=True)
class BulkArchiveDocuments:
    repo: DocumentRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: BulkArchiveCommand) -> BulkArchiveResult:
        validate_bulk_count(len(cmd.ids))

        now = self.clock.now()
        archived_count, skipped_count = await self.repo.bulk_archive(cmd.ids, now)

        # Emit a single bulk-archive event listing all submitted IDs (not per-document) to
        # avoid N+1 outbox rows. The repo returns only counts — fetching which specific IDs
        # were archived vs skipped would require an additional SELECT. Notification-service
        # and audit-service can join on the submitted IDs with their own projections.
        # US-23 AC: no duplicate events for already-archived (skipped) rows — satisfied
        # because we emit only when archived_count > 0, and the event carries count separately.
        if archived_count > 0:
            event = DocumentBulkArchived(
                document_ids=cmd.ids,
                count=archived_count,
                actor_id=cmd.actor_id,
                request_id=cmd.request_id,
                occurred_at=now,
            )
            await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return BulkArchiveResult(archived=archived_count, skipped=skipped_count)
