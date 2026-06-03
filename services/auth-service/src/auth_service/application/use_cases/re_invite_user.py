# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ReInviteUser use case — US-10.

Invalidates old OTP and issues a new one for a user who:
  - exists in auth.users
  - has no TOTP secret (totp_secret_enc == TOTP_SENTINEL)
  - has must_change_password=True

If the user already has a TOTP secret → raises UserAlreadyActivatedError (409).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from auth_service.application.dto import (
    InviteUserAutoDTO,
    InviteUserShowOtpDTO,
    ReInviteUserCommand,
)
from auth_service.application.ports import (
    ChannelDirectoryReader,
    EventOutbox,
    InviteOtpPublisher,
    PasswordHasher,
    UserRepository,
)
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import (
    NoEnabledChannelError,
    UserAlreadyActivatedError,
    UserNotFoundError,
)
from auth_service.domain.events import InvitationResent, InviteRequested

_CHANNEL_PRIORITY = ["email", "telegram", "dion"]


@dataclass(slots=True)
class ReInviteUser:
    """Re-send an invitation to a pending (not-yet-enrolled) user."""

    user_repo: UserRepository
    hasher: PasswordHasher
    outbox: EventOutbox
    channel_reader: ChannelDirectoryReader
    otp_publisher: InviteOtpPublisher

    async def execute(
        self, *, cmd: ReInviteUserCommand
    ) -> InviteUserAutoDTO | InviteUserShowOtpDTO:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        # Block re-invite for already-activated users (TOTP enrolled).
        if user.totp_secret_enc != TOTP_SENTINEL:
            raise UserAlreadyActivatedError()

        # For auto delivery: check channels before changing anything.
        channel_used: str | None = None
        if cmd.delivery == "auto":
            enabled = await self.channel_reader.get_enabled_channels()
            for ch in _CHANNEL_PRIORITY:
                if ch in enabled:
                    channel_used = ch
                    break
            if channel_used is None:
                raise NoEnabledChannelError()

        # Generate new OTP and update password_hash (old OTP invalidated).
        new_otp = secrets.token_urlsafe(9)
        user.password_hash = self.hasher.hash(new_otp)
        await self.user_repo.update(user)

        # Emit InvitationResent audit event.
        await self.outbox.publish(
            InvitationResent(
                actor_id=cmd.actor_id,
                user_id=user.id,
                email=user.email,
            ).as_envelope()
        )

        if cmd.delivery == "auto":
            assert channel_used is not None
            invitation_id = uuid.uuid4()

            # Store OTP side-channel in Redis (F-001: OTP must NOT enter the outbox).
            await self.otp_publisher.publish(invitation_id, new_otp)

            await self.outbox.publish(
                InviteRequested(
                    actor_id=cmd.actor_id,
                    user_id=user.id,
                    email=user.email,
                    role=user.role,
                    otp=new_otp,  # stored in domain event for in-process use only; not in envelope
                    login_url=cmd.login_url,
                    channel_preference=channel_used,
                    invitation_id=invitation_id,
                ).as_envelope()
            )

            return InviteUserAutoDTO(
                user_id=user.id,
                channel_used=channel_used,
                invitation_id=invitation_id,
            )
        else:
            return InviteUserShowOtpDTO(
                user_id=user.id,
                otp=new_otp,
                otp_ttl_minutes=10,
            )
