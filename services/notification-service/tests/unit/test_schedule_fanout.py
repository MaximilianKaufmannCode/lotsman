# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for reminder fan-out to all active users (ADR-0011 §D4)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from notification_service.application.use_cases.schedule_daily_reminders import (
    ScheduleDailyReminders,
)

TODAY = date(2026, 6, 1)
RESP = str(uuid.uuid4())
U1 = str(uuid.uuid4())
U2 = str(uuid.uuid4())
SYS = str(uuid.uuid4())
DOC = str(uuid.uuid4())


class _FakeRegistry:
    async def list_active_documents(self) -> list[dict]:
        return [
            {
                "id": DOC,
                "type_code": "contract",
                "expiry_date": "2026-06-01",  # due today → in_day
                "status": "active",
                "responsible_user_id": RESP,
            }
        ]

    async def get_document_type(self, tc: str) -> dict:
        return {"pre_notice_days": [30, 7, 1], "notify_in_day": True, "overdue_every_days": 7}


class _FakeAuth:
    def __init__(self, users: list[dict]) -> None:
        self._users = users

    async def list_active_users(self) -> list[dict]:
        return self._users


class _FakeArq:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue_job(self, name: str, *args, _job_id: str | None = None) -> None:
        self.calls.append((name, args, _job_id))


def _recipients(arq: _FakeArq) -> set[str]:
    return {args[1] for (_n, args, _j) in arq.calls}


@pytest.mark.asyncio
async def test_fan_out_to_all_active_excluding_system() -> None:
    arq = _FakeArq()
    auth = _FakeAuth(
        [
            {"id": U1, "email": "a@example.com", "is_active": True},
            {"id": U2, "email": "b@example.com", "is_active": True},
            {"id": SYS, "email": "scheduler@system.lotsman", "is_active": False},
        ]
    )
    uc = ScheduleDailyReminders(
        registry_gateway=_FakeRegistry(),
        arq_pool=arq,
        auth_gateway=auth,
        session_factory=None,  # no prefs rows → defaults (deadline email on)
    )
    result = await uc.execute(today=TODAY)

    assert result["enqueued"] == 2
    assert _recipients(arq) == {U1, U2}  # system account excluded
    assert all(name == "send_document_reminder" for (name, _a, _j) in arq.calls)
    # per-user idempotency job id includes the user id
    assert any(U1 in (j or "") for (_n, _a, j) in arq.calls)


@pytest.mark.asyncio
async def test_legacy_fallback_when_no_auth_gateway() -> None:
    arq = _FakeArq()
    uc = ScheduleDailyReminders(registry_gateway=_FakeRegistry(), arq_pool=arq)
    result = await uc.execute(today=TODAY)
    assert result["enqueued"] == 1
    assert _recipients(arq) == {RESP}


@pytest.mark.asyncio
async def test_empty_user_list_falls_back_to_responsible() -> None:
    arq = _FakeArq()
    uc = ScheduleDailyReminders(
        registry_gateway=_FakeRegistry(),
        arq_pool=arq,
        auth_gateway=_FakeAuth([]),  # auth down / empty → must not drop reminders
        session_factory=None,
    )
    result = await uc.execute(today=TODAY)
    assert result["enqueued"] == 1
    assert _recipients(arq) == {RESP}
