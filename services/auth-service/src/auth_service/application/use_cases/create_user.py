# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CreateUser use case — US-17 (admin whitelist-based invite).

Creates a new user with:
- Argon2id hash of a generated OOB OTP as the initial password_hash
- totp_secret_enc = TOTP_SENTINEL (not yet enrolled)
- must_change_password = True
- OOB OTP returned to admin for out-of-band delivery
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from auth_service.application.dto import CreateUserCommand, CreateUserDTO
from auth_service.application.ports import EventOutbox, PasswordHasher, UserRepository
from auth_service.domain.entities import User
from auth_service.domain.errors import UserAlreadyExistsError
from auth_service.domain.events import UserCreated
from auth_service.domain.value_objects import Email


@dataclass(slots=True)
class CreateUser:
    user_repo: UserRepository
    hasher: PasswordHasher
    outbox: EventOutbox

    async def execute(self, *, cmd: CreateUserCommand) -> CreateUserDTO:
        # Validate and normalise email
        try:
            email_vo = Email(value=cmd.email)
        except Exception as exc:
            raise UserAlreadyExistsError(str(exc)) from exc

        # Check for existing active user with this email
        existing = await self.user_repo.get_by_email(email_vo.value)
        if existing is not None and existing.deleted_at is None:
            raise UserAlreadyExistsError()

        # Generate OOB OTP (12 URL-safe base64 chars ≈ 72 bits entropy)
        oob_otp = secrets.token_urlsafe(9)
        password_hash = self.hasher.hash(oob_otp)

        user = User.create_new(
            email=email_vo.value,
            full_name=cmd.full_name,
            password_hash=password_hash,
            role=cmd.role,
        )
        await self.user_repo.add(user)

        await self.outbox.publish(
            UserCreated(
                actor_id=cmd.actor_id,
                user_id=user.id,
                email=user.email,
                role=user.role,
            ).as_envelope()
        )

        return CreateUserDTO(user_id=user.id, oob_otp=oob_otp)
