# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for DisableChannel — US-6 happy path, US-6.2 pending-invites block."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest


@dataclass
class FakeRow:
    channel: str
    enabled: bool
    config_enc: bytes = b"encrypted"
    created_by: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
    created_at: datetime = datetime.now(tz=UTC)
    updated_at: datetime = datetime.now(tz=UTC)


class FakeCredentialRepo:
    def __init__(self, rows: list[FakeRow]) -> None:
        self._rows = rows
        self.set_enabled_calls: list[tuple[str, bool]] = []

    async def get_all(self) -> list[Any]:
        return list(self._rows)

    async def upsert(self, **kwargs: Any) -> None:
        pass

    async def set_enabled(self, *, channel: str, enabled: bool) -> None:
        self.set_enabled_calls.append((channel, enabled))


class FakeInviteStore:
    def __init__(self, has_pending: bool) -> None:
        self._has_pending = has_pending

    async def has_pending_invites(self) -> bool:
        return self._has_pending


class FakeOutbox:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, envelope: Any) -> None:
        self.events.append(envelope)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


@pytest.mark.asyncio
async def test_disable_channel_happy_path() -> None:
    """US-6: Disable email — config preserved, event emitted."""
    from notification_service.application.use_cases.disable_channel import DisableChannel

    row = FakeRow(channel="email", enabled=True)
    repo = FakeCredentialRepo([row])
    invite_store = FakeInviteStore(has_pending=False)
    outbox = FakeOutbox()

    use_case = DisableChannel(
        credential_repo=repo,
        invite_store=invite_store,
        outbox=outbox,
    )
    await use_case.execute(actor_id=uuid.uuid4(), channel="email")

    assert ("email", False) in repo.set_enabled_calls
    assert "notification.channel.disabled.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_disable_last_channel_with_pending_invites() -> None:
    """US-6.2: Cannot disable last enabled channel if pending invitations exist."""
    from notification_service.application.use_cases.disable_channel import DisableChannel
    from notification_service.domain.errors import PendingInvitationsError

    row = FakeRow(channel="email", enabled=True)
    repo = FakeCredentialRepo([row])
    invite_store = FakeInviteStore(has_pending=True)
    outbox = FakeOutbox()

    use_case = DisableChannel(
        credential_repo=repo,
        invite_store=invite_store,
        outbox=outbox,
    )

    with pytest.raises(PendingInvitationsError):
        await use_case.execute(actor_id=uuid.uuid4(), channel="email")

    # Nothing persisted.
    assert len(repo.set_enabled_calls) == 0


@pytest.mark.asyncio
async def test_disable_one_of_two_channels() -> None:
    """Disabling one channel when another is also enabled → no pending-invites check."""
    from notification_service.application.use_cases.disable_channel import DisableChannel

    rows = [
        FakeRow(channel="email", enabled=True),
        FakeRow(channel="telegram", enabled=True),
    ]
    repo = FakeCredentialRepo(rows)
    # Invite store would block if asked, but it shouldn't be called.
    invite_store = FakeInviteStore(has_pending=True)
    outbox = FakeOutbox()

    use_case = DisableChannel(
        credential_repo=repo,
        invite_store=invite_store,
        outbox=outbox,
    )
    # Should succeed because telegram is also enabled.
    await use_case.execute(actor_id=uuid.uuid4(), channel="email")
    assert ("email", False) in repo.set_enabled_calls
