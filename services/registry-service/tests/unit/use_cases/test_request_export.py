# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for RequestExport use case (US-20) and DownloadExport (US-21)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import RequestExportCommand
from registry_service.application.use_cases.download_export import DownloadExport
from registry_service.application.use_cases.request_export import RequestExport
from registry_service.domain.entities import ExportJob
from registry_service.domain.errors import ExportJobExpiredError, ExportJobNotFoundError
from tests.unit.use_cases.fakes import FakeClock, FakeEventOutbox, FakeExportJobRepository


@pytest.mark.asyncio
async def test_request_export_creates_job() -> None:
    """Happy path: job row created, status=pending, outbox event emitted."""
    repo = FakeExportJobRepository()
    outbox = FakeEventOutbox()
    use_case = RequestExport(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    actor_id = uuid.uuid4()
    dto = await use_case.execute(
        cmd=RequestExportCommand(
            filters={"status": "active"},
            visible_columns=["number", "asset_id", "expiry_date"],
            actor_id=actor_id,
        )
    )

    assert dto.status == "pending"
    assert dto.file_path is None
    assert dto.error is None

    stored = await repo.get_by_id(dto.id)
    assert stored is not None
    assert stored.requested_by == actor_id

    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.export.requested.v1"
    assert topic == "registry.exports"
    assert envelope.actor_id == actor_id


@pytest.mark.asyncio
async def test_request_export_returns_job_id() -> None:
    """The returned DTO.id must match the persisted job."""
    repo = FakeExportJobRepository()
    use_case = RequestExport(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    dto = await use_case.execute(
        cmd=RequestExportCommand(
            filters={},
            visible_columns=[],
            actor_id=uuid.uuid4(),
        )
    )

    stored = await repo.get_by_id(dto.id)
    assert stored is not None


# ---------------------------------------------------------------------------
# DownloadExport
# ---------------------------------------------------------------------------


def _make_completed_job(*, expires_at: datetime) -> ExportJob:
    now = datetime.now(tz=UTC)
    job = ExportJob(
        id=uuid.uuid4(),
        requested_by=uuid.uuid4(),
        status="done",
        file_path="/exports/test.xlsx",
        error=None,
        filters={},
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    return job


@pytest.mark.asyncio
async def test_download_export_not_found_raises() -> None:
    repo = FakeExportJobRepository()
    use_case = DownloadExport(repo=repo, storage=None, clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(ExportJobNotFoundError):
        await use_case.get_download_url(job_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_download_export_expired_raises() -> None:
    """An expired export job must raise ExportJobExpiredError (Q8, HTTP 410)."""
    # Use an expiry date clearly in the past regardless of wall-clock time.
    expired_at = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    job = _make_completed_job(expires_at=expired_at)

    repo = FakeExportJobRepository()
    await repo.add(job)

    use_case = DownloadExport(repo=repo, storage=None, clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(ExportJobExpiredError):
        await use_case.get_download_url(job_id=job.id)
