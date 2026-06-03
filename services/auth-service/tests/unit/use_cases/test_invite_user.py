# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for InviteUser — US-8.2, US-8.3, US-9."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import (
    InviteUserAutoDTO,
    InviteUserCommand,
    InviteUserShowOtpDTO,
)
from auth_service.application.use_cases.invite_user import InviteUser
from auth_service.domain.errors import NoEnabledChannelError, UserAlreadyExistsError

from .conftest import (
    FakeEventOutbox,
    FakePasswordHasher,
    FakeUserRepository,
    make_user,
)


class FakeChannelReader:
    def __init__(self, channels: list[str]) -> None:
        self._channels = channels

    async def get_enabled_channels(self) -> list[str]:
        return self._channels


class FakeInviteOtpPublisher:
    """Records publish calls for assertion in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, int]] = []

    async def publish(
        self,
        invitation_id: uuid.UUID,
        otp: str,
        ttl_seconds: int = 600,
    ) -> None:
        self.calls.append((invitation_id, otp, ttl_seconds))


@pytest.fixture
def actor_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_invite_auto_happy_path_email_priority(actor_id: uuid.UUID) -> None:
    """US-8.2: auto delivery selects email over telegram (priority order)."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader(["telegram", "email"])  # email wins by priority
    otp_publisher = FakeInviteOtpPublisher()

    use_case = InviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    cmd = InviteUserCommand(
        email="new@org.local",
        full_name="New User",
        role="editor",
        delivery="auto",
        actor_id=actor_id,
    )
    result = await use_case.execute(cmd=cmd)

    assert isinstance(result, InviteUserAutoDTO)
    assert result.channel_used == "email"
    assert result.user_id is not None
    # OTP must NOT be in result.
    assert not hasattr(result, "otp")
    # Events emitted.
    types = outbox.event_types()
    assert "auth.user.invited.v1" in types
    assert "notification.invite.requested.v1" in types

    # F-001: OTP must NOT be in the outbox envelope payload.
    invite_envelope = next(e for e in outbox.events if e.type == "notification.invite.requested.v1")
    assert "otp" not in invite_envelope.payload, (
        "OTP must not be persisted in the outbox payload (F-001 / ADR-0004 §6)"
    )

    # F-001: OTP must be published via the side-channel publisher.
    assert len(otp_publisher.calls) == 1
    pub_invitation_id, pub_otp, pub_ttl = otp_publisher.calls[0]
    assert pub_invitation_id == result.invitation_id
    assert len(pub_otp) > 0
    assert pub_ttl == 600


@pytest.mark.asyncio
async def test_invite_auto_no_channel_blocks(actor_id: uuid.UUID) -> None:
    """US-8.3: auto delivery with no enabled channels → 409 NoEnabledChannelError."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader([])
    otp_publisher = FakeInviteOtpPublisher()

    use_case = InviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    cmd = InviteUserCommand(
        email="new@org.local",
        full_name="New User",
        role="editor",
        delivery="auto",
        actor_id=actor_id,
    )

    with pytest.raises(NoEnabledChannelError):
        await use_case.execute(cmd=cmd)

    # No user created on failure.
    assert len(repo._store) == 0


@pytest.mark.asyncio
async def test_invite_show_otp(actor_id: uuid.UUID) -> None:
    """US-9: show-otp delivery returns OTP in response, no notification event."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader([])  # no channels — doesn't matter for show-otp
    otp_publisher = FakeInviteOtpPublisher()

    use_case = InviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    cmd = InviteUserCommand(
        email="new@org.local",
        full_name="New User",
        role="editor",
        delivery="show-otp",
        actor_id=actor_id,
    )
    result = await use_case.execute(cmd=cmd)

    assert isinstance(result, InviteUserShowOtpDTO)
    assert result.otp is not None and len(result.otp) > 0
    assert result.otp_ttl_minutes == 10
    # No channel notification event.
    assert "notification.invite.requested.v1" not in outbox.event_types()
    assert "auth.user.invited.v1" in outbox.event_types()
    # show-otp path does not use the side-channel publisher.
    assert len(otp_publisher.calls) == 0


@pytest.mark.asyncio
async def test_invite_duplicate_email(actor_id: uuid.UUID) -> None:
    """Duplicate email raises UserAlreadyExistsError."""
    repo = FakeUserRepository()
    existing = make_user(email="existing@org.local", role="editor")
    await repo.add(existing)

    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader(["email"])
    otp_publisher = FakeInviteOtpPublisher()

    use_case = InviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    with pytest.raises(UserAlreadyExistsError):
        await use_case.execute(
            cmd=InviteUserCommand(
                email="existing@org.local",
                full_name="Duplicate",
                role="editor",
                delivery="auto",
                actor_id=actor_id,
            )
        )
