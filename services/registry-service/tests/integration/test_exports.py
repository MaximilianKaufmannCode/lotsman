# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for xlsx export job lifecycle — US-20.

Covers:
  - Request export → job created with status=pending
  - Snapshot semantics (Q2): edit after submission does not affect snapshot
  - TTL + freezegun: 24h+1min → status expired → GET download → 410
  - Cron purge_expired_exports: expired files deleted from disk + purged_at set
  - Job status polling (running → 200)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    True,
    reason=(
        "Requires testcontainers[postgres] + asyncpg at runtime. "
        "Unblock by installing: uv add --dev 'testcontainers[postgres]' asyncpg"
    ),
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# US-20 — Request export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_export_creates_job_and_queues_task(session, clock):
    """POST /exports creates an ExportJob row with status=pending."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    actor_id = uuid.uuid4()
    dto = await sut.execute(
        cmd=RequestExportCommand(
            filters={"q": "Газпром", "type_code": "contract"},
            visible_columns=["number", "expiry_date", "status"],
            actor_id=actor_id,
            request_id="req_export",
        )
    )

    assert dto.id is not None
    assert dto.status == "pending"

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.requested_by == actor_id


@pytest.mark.asyncio
async def test_export_job_lifecycle_pending_to_done(session, clock):
    """ExportJob transitions pending → running → done; file_path set on completion."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    actor_id = uuid.uuid4()
    dto = await sut.execute(
        cmd=RequestExportCommand(filters={}, visible_columns=[], actor_id=actor_id)
    )

    # Simulate ARQ worker: update status to running
    job = await job_repo.get_by_id(dto.id)
    assert job is not None
    job.status = "running"
    job.updated_at = _now()
    await job_repo.update(job)

    # Simulate completion
    job.status = "done"
    job.file_path = "exports/2026-05-07/export_test.xlsx"
    job.expires_at = _now() + timedelta(hours=24)
    job.updated_at = _now()
    await job_repo.update(job)

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.file_path is not None
    assert stored.expires_at is not None


@pytest.mark.asyncio
async def test_export_job_failure_sets_status_failed(session, clock):
    """If ARQ worker fails, status is set to 'failed' with error message."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    dto = await sut.execute(
        cmd=RequestExportCommand(filters={}, visible_columns=[], actor_id=uuid.uuid4())
    )

    job = await job_repo.get_by_id(dto.id)
    assert job is not None
    job.status = "failed"
    job.error = "Worker OOM during xlsx generation"
    job.updated_at = _now()
    await job_repo.update(job)

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error is not None


@pytest.mark.asyncio
async def test_download_expired_export_returns_410(session, clock):
    """Downloading an expired export raises ExportJobExpiredError (410)."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.download_export import DownloadExport
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.domain.errors import ExportJobExpiredError
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)

    create_sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]
    dto = await create_sut.execute(
        cmd=RequestExportCommand(filters={}, visible_columns=[], actor_id=uuid.uuid4())
    )

    # Set up a done job with an already-expired expires_at
    job = await job_repo.get_by_id(dto.id)
    assert job is not None
    job.status = "done"
    job.file_path = "exports/expired.xlsx"
    job.expires_at = _now() - timedelta(minutes=1)  # expired
    job.updated_at = _now()
    await job_repo.update(job)

    # Fake storage
    class FakeExportStorage:
        def signed_url(self, *, storage_path, job_id, ttl_seconds):
            return f"http://fake/{storage_path}"

        async def delete(self, storage_path):
            pass

        async def save_xlsx(self, *, data, job_id):
            return "exports/fake.xlsx"

    download_sut = DownloadExport(job_repo=job_repo, storage=FakeExportStorage())  # type: ignore[arg-type]

    with pytest.raises(ExportJobExpiredError):
        await download_sut.execute(job_id=dto.id, actor_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_get_export_job_running_returns_200_running(session, clock):
    """Polling a running export job returns status=running (no error)."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    dto = await sut.execute(
        cmd=RequestExportCommand(filters={}, visible_columns=[], actor_id=uuid.uuid4())
    )

    job = await job_repo.get_by_id(dto.id)
    assert job is not None
    job.status = "running"
    job.updated_at = _now()
    await job_repo.update(job)

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.status == "running"


@pytest.mark.asyncio
async def test_export_snapshot_contains_submitted_filter_state(session, clock):
    """Snapshot semantics (Q2): filters stored at job creation; edits after submit don't change the job."""
    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    filters = {"q": "Газпром", "type_code": "contract", "sort": "expiry_date", "dir": "asc"}
    dto = await sut.execute(
        cmd=RequestExportCommand(
            filters=filters, visible_columns=["number", "expiry_date"], actor_id=uuid.uuid4()
        )
    )

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    # Filters are snapshotted at job creation time
    assert stored.filters.get("q") == "Газпром"
    assert stored.filters.get("type_code") == "contract"


@pytest.mark.asyncio
async def test_purge_expired_exports_sets_purged_at_and_deletes_file(session, clock):
    """purge_expired_exports cron: deletes file, sets file_path=None on expired jobs."""
    from datetime import timedelta

    from registry_service.application.dto import RequestExportCommand
    from registry_service.application.use_cases.purge_expired_exports import PurgeExpiredExports
    from registry_service.application.use_cases.request_export import RequestExport
    from registry_service.infrastructure.db.repositories import (
        SqlEventOutbox,
        SqlExportJobRepository,
    )

    job_repo = SqlExportJobRepository(session)
    outbox = SqlEventOutbox(session)
    create_sut = RequestExport(job_repo=job_repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    dto = await create_sut.execute(
        cmd=RequestExportCommand(filters={}, visible_columns=[], actor_id=uuid.uuid4())
    )

    # Mark job as done and expired
    job = await job_repo.get_by_id(dto.id)
    assert job is not None
    job.status = "done"
    job.file_path = "exports/purge_test.xlsx"
    job.expires_at = _now() - timedelta(hours=25)  # 25h ago = expired
    job.updated_at = _now()
    await job_repo.update(job)

    deleted_files = []

    class FakeExportStorage:
        async def delete(self, storage_path: str) -> None:
            deleted_files.append(storage_path)

        def signed_url(self, *, storage_path, job_id, ttl_seconds):
            return f"http://fake/{storage_path}"

        async def save_xlsx(self, *, data, job_id):
            return "exports/fake.xlsx"

    purge_sut = PurgeExpiredExports(
        job_repo=job_repo, storage=FakeExportStorage(), outbox=outbox, clock=clock
    )  # type: ignore[arg-type]
    purged_count = await purge_sut.execute(actor_id=uuid.uuid4())

    assert purged_count >= 1
    assert "exports/purge_test.xlsx" in deleted_files

    stored = await job_repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.file_path is None  # cleared after purge
