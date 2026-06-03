# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ReInviteUser — US-10."""

from __future__ import annotations

import uuid

import pytest

from auth_service.application.dto import (
    InviteUserAutoDTO,
    ReInviteUserCommand,
)
from auth_service.application.use_cases.re_invite_user import ReInviteUser
from auth_service.domain.errors import UserAlreadyActivatedError, UserNotFoundError

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


@pytest.mark.asyncio
async def test_re_invite_user_auto_success() -> None:
    """US-10: re-invite invalidates old OTP and emits new invite event."""

    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader(["email"])
    otp_publisher = FakeInviteOtpPublisher()

    user = make_user(role="editor", email="pending@org.local", has_totp=False)
    # Ensure must_change_password (simulates pending state).
    user.must_change_password = True
    await repo.add(user)
    old_hash = user.password_hash

    use_case = ReInviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    result = await use_case.execute(
        cmd=ReInviteUserCommand(
            target_user_id=user.id,
            actor_id=uuid.uuid4(),
            delivery="auto",
        )
    )

    assert isinstance(result, InviteUserAutoDTO)
    assert result.channel_used == "email"
    # Password hash updated (old OTP invalidated).
    updated = await repo.get_by_id(user.id)
    assert updated is not None
    assert updated.password_hash != old_hash

    types = outbox.event_types()
    assert "auth.invitation.resent.v1" in types
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
async def test_re_invite_activated_user_blocked() -> None:
    """US-10: Re-invite blocked for user who already enrolled TOTP."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader(["email"])
    otp_publisher = FakeInviteOtpPublisher()

    # User with TOTP enrolled.
    user = make_user(role="editor", email="active@org.local", has_totp=True)
    await repo.add(user)

    use_case = ReInviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    with pytest.raises(UserAlreadyActivatedError):
        await use_case.execute(
            cmd=ReInviteUserCommand(
                target_user_id=user.id,
                actor_id=uuid.uuid4(),
                delivery="auto",
            )
        )


@pytest.mark.asyncio
async def test_re_invite_nonexistent_user() -> None:
    """Re-invite of non-existent user raises UserNotFoundError."""
    repo = FakeUserRepository()
    outbox = FakeEventOutbox()
    hasher = FakePasswordHasher()
    channel_reader = FakeChannelReader(["email"])
    otp_publisher = FakeInviteOtpPublisher()

    use_case = ReInviteUser(
        user_repo=repo,
        hasher=hasher,
        outbox=outbox,
        channel_reader=channel_reader,
        otp_publisher=otp_publisher,
    )
    with pytest.raises(UserNotFoundError):
        await use_case.execute(
            cmd=ReInviteUserCommand(
                target_user_id=uuid.uuid4(),
                actor_id=uuid.uuid4(),
                delivery="auto",
            )
        )
