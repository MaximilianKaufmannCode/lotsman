# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ARQ task definitions for registry-service.

Tasks registered here:
  - run_export_job:        Generate xlsx for an ExportJob (US-20).
  - purge_expired_exports: Hourly cron to delete expired export files (Q8).
  - dispatch_outbox:       Re-exported from outbox/dispatcher.py.

The ARQ worker context (ctx) must have:
  - ctx["session_factory"]: async_sessionmaker[AsyncSession]
  - ctx["redis"]:           aioredis.Redis
  - ctx["settings"]:        Settings

These are injected in the ARQ WorkerSettings defined in this module.
"""

from __future__ import annotations

import uuid
from typing import Any

import os

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_service.application.policies.export_policy import compute_expires_at
from registry_service.infrastructure.outbox.dispatcher import dispatch_outbox

log = structlog.get_logger(__name__)


async def run_export_job(ctx: dict[str, Any], *, job_id: str) -> None:
    """ARQ task: generate xlsx for an export job.

    Snapshot semantics (Q2): the query runs at job start, capturing the
    data_snapshot_at = now at the moment this task begins execution.
    """
    from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER

    from registry_service.domain.events import ExportJobCompleted
    from registry_service.infrastructure.clock import SystemClock
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlEventOutbox,
        SqlExportJobRepository,
    )
    from registry_service.infrastructure.storage.local_filesystem import LocalFilesystemStorage
    from registry_service.infrastructure.xlsx_exporter import OpenpyxlExporter

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings = ctx["settings"]
    clock = SystemClock()

    async with session_factory() as session, session.begin():
        job_repo = SqlExportJobRepository(session)
        job = await job_repo.get_by_id(uuid.UUID(job_id))
        if job is None:
            log.warning("export_job_not_found", job_id=job_id)
            return

        # Mark as running
        job.status = "running"
        job.updated_at = clock.now()
        await job_repo.update(job)

        outbox = SqlEventOutbox(session)
        filters = job.filters or {}
        filter_params = filters.get("filters", {})
        visible_columns = filters.get("visible_columns", [])

        try:
            doc_repo = SqlDocumentRepository(session)
            asset_repo = SqlAssetRepository(session)

            snapshot_at = clock.now()
            documents = await doc_repo.list_active(
                asset_id=filter_params.get("asset_id"),
                type_code=filter_params.get("type_code"),
                q=filter_params.get("q"),
                sort=filter_params.get("sort"),
                dir=filter_params.get("dir"),
                offset=0,
                limit=200_000,  # large limit — export all matching rows
            )

            # Collect all referenced assets
            asset_ids = {d.asset_id for d in documents}
            assets: dict[uuid.UUID, Any] = {}
            for aid in asset_ids:
                a = await asset_repo.get_by_id(aid)
                if a:
                    assets[aid] = a

            exporter = OpenpyxlExporter()
            xlsx_bytes = await exporter.export(
                documents=documents,
                assets=assets,
                visible_columns=visible_columns,
                snapshot_at=snapshot_at,
            )

            # Save file to exports volume
            volume_root = getattr(settings, "exports_volume_root", "/vol/exports")
            storage = LocalFilesystemStorage(volume_root)
            storage_path = await storage.save(
                data=xlsx_bytes,
                document_id=uuid.UUID(int=0),  # not document-scoped
                attachment_id=uuid.UUID(job_id),
                original_filename=f"Лоцман_реестр_{snapshot_at.date().isoformat()}.xlsx",
            )

            now = clock.now()
            job.status = "done"
            job.file_path = storage_path
            job.expires_at = compute_expires_at(now)
            job.updated_at = now
            await job_repo.update(job)

            event = ExportJobCompleted(
                job_id=uuid.UUID(job_id),
                file_path=storage_path,
                actor_id=ACTOR_OUTBOX_DISPATCHER,
                occurred_at=now,
            )
            await outbox.publish(event.as_envelope(), topic=event.topic)

            log.info(
                "export_job_completed",
                job_id=job_id,
                doc_count=len(documents),
                size_bytes=len(xlsx_bytes),
            )

        except Exception as exc:
            log.exception("export_job_failed", job_id=job_id)
            now = clock.now()
            job.status = "failed"
            job.error = str(exc)
            job.updated_at = now
            await job_repo.update(job)

            from registry_service.domain.events import ExportJobFailed as ExportFailed

            event_fail = ExportFailed(
                job_id=uuid.UUID(job_id),
                error=str(exc),
                actor_id=ACTOR_OUTBOX_DISPATCHER,
                occurred_at=now,
            )
            await outbox.publish(event_fail.as_envelope(), topic=event_fail.topic)


async def purge_expired_exports(ctx: dict[str, Any]) -> None:
    """ARQ cron task: purge export files older than 24 hours (Q8)."""
    from registry_service.application.use_cases.purge_expired_exports import PurgeExpiredExports
    from registry_service.infrastructure.clock import SystemClock
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )
    from registry_service.infrastructure.storage.local_filesystem import LocalFilesystemStorage

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings = ctx["settings"]

    async with session_factory() as session, session.begin():
        job_repo = SqlExportJobRepository(session)
        outbox = SqlEventOutbox(session)
        volume_root = getattr(settings, "exports_volume_root", "/vol/exports")
        storage = LocalFilesystemStorage(volume_root)
        clock = SystemClock()

        use_case = PurgeExpiredExports(
            repo=job_repo,
            storage=storage,  # type: ignore[arg-type]
            outbox=outbox,  # type: ignore[arg-type]
            clock=clock,
        )
        count = await use_case.execute()
        log.info("export_purge_complete", purged=count)


class WorkerSettings:
    """ARQ worker configuration for registry-service."""

    redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )

    # Per-service queue isolation (see notification-service WorkerSettings for context).
    queue_name = "arq:registry"

    functions = [run_export_job, purge_expired_exports, dispatch_outbox]

    cron_jobs = [
        cron(
            dispatch_outbox,
            name="dispatch_outbox",
            second={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
            unique=True,
        ),
        cron(
            purge_expired_exports,
            name="purge_expired_exports",
            minute=0,
            unique=True,
        ),
    ]

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        import redis.asyncio as aioredis

        from registry_service.config import get_settings
        from registry_service.infrastructure.db.session import (
            init_engine,
        )

        settings = get_settings()
        ctx["settings"] = settings

        engine = init_engine(settings.database_url)
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        ctx["session_factory"] = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        from registry_service.infrastructure.db.session import dispose_engine

        await dispose_engine()
        await ctx["redis"].aclose()
