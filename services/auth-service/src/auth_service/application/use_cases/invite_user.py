# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""InviteUser use case — US-8, US-9 (ADR-0004 §5 / Phase 2b).

Extends CreateUser with delivery channel support:
  - delivery='auto': selects the first enabled channel (email > telegram > dion),
    emits notification.invite.requested.v1, returns {user_id, channel_used, invitation_id}.
  - delivery='show-otp': returns OTP in the response body (no notification event).

If delivery='auto' and no channel is enabled → raises NoEnabledChannelError (409).
User creation is NOT performed before channel check to avoid orphaned users (US-8.3).

Re-invite (US-10) is a separate use case (re_invite_user.py).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from auth_service.application.dto import (
    InviteUserAutoDTO,
    InviteUserCommand,
    InviteUserShowOtpDTO,
)
from auth_service.application.ports import (
    ChannelDirectoryReader,
    EventOutbox,
    InviteOtpPublisher,
    PasswordHasher,
    UserRepository,
)
from auth_service.domain.entities import User
from auth_service.domain.errors import NoEnabledChannelError, UserAlreadyExistsError
from auth_service.domain.events import InviteRequested, UserInvited
from auth_service.domain.value_objects import Email

# Channel delivery priority (ADR-0004 §5)
_CHANNEL_PRIORITY = ["email", "telegram", "dion"]


@dataclass(slots=True)
class InviteUser:
    """Create a new user and optionally send the OTP via a notification channel."""

    user_repo: UserRepository
    hasher: PasswordHasher
    outbox: EventOutbox
    channel_reader: ChannelDirectoryReader
    otp_publisher: InviteOtpPublisher

    async def execute(self, *, cmd: InviteUserCommand) -> InviteUserAutoDTO | InviteUserShowOtpDTO:
        # 1. For auto delivery: check enabled channels BEFORE creating the user.
        channel_used: str | None = None
        if cmd.delivery == "auto":
            enabled = await self.channel_reader.get_enabled_channels()
            channel_used = _pick_channel(enabled)
            if channel_used is None:
                raise NoEnabledChannelError()

        # 2. Validate and normalise email.
        try:
            email_vo = Email(value=cmd.email)
        except Exception as exc:
            raise UserAlreadyExistsError(str(exc)) from exc

        # 3. Check for existing active user.
        existing = await self.user_repo.get_by_email(email_vo.value)
        if existing is not None and existing.deleted_at is None:
            raise UserAlreadyExistsError()

        # 4. Generate OOB OTP.
        oob_otp = _generate_otp()
        password_hash = self.hasher.hash(oob_otp)

        # 5. Create user entity.
        user = User.create_new(
            email=email_vo.value,
            full_name=cmd.full_name,
            password_hash=password_hash,
            role=cmd.role,
        )
        await self.user_repo.add(user)

        # 6. Emit UserInvited audit event.
        await self.outbox.publish(
            UserInvited(
                actor_id=cmd.actor_id,
                user_id=user.id,
                email=user.email,
                role=user.role,
                delivery=cmd.delivery,
                channel_used=channel_used,
            ).as_envelope()
        )

        if cmd.delivery == "auto":
            assert channel_used is not None
            invitation_id = uuid.uuid4()

            # 7. Store OTP side-channel in Redis (F-001: OTP must NOT enter the outbox).
            await self.otp_publisher.publish(invitation_id, oob_otp)

            # 8. Emit invite-requested event for notification-service consumer.
            #    The OTP is NOT in the envelope; consumer reads it from Redis by invitation_id.
            await self.outbox.publish(
                InviteRequested(
                    actor_id=cmd.actor_id,
                    user_id=user.id,
                    email=user.email,
                    role=user.role,
                    otp=oob_otp,  # stored in domain event for in-process use only; not in envelope
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
            # show-otp: return OTP in response, no notification event.
            return InviteUserShowOtpDTO(
                user_id=user.id,
                otp=oob_otp,
                otp_ttl_minutes=10,
            )


def _pick_channel(enabled: list[str]) -> str | None:
    """Select first enabled channel by priority (email > telegram > dion)."""
    for ch in _CHANNEL_PRIORITY:
        if ch in enabled:
            return ch
    return None


def _generate_otp() -> str:
    """Generate a 12 URL-safe base64 char OTP (≈72 bits entropy)."""
    return secrets.token_urlsafe(9)
