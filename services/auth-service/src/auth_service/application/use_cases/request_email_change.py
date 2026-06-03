# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RequestEmailChange use case — self-service email change, step 1.

Input:  RequestEmailChangeCommand(actor_id, new_email)
Output: EmailChangeRequestedDTO(request_id, code_ttl_seconds, masked_new_email)

Business rules:
  1. new_email normalised to lowercase.
  2. Must pass a permissive RFC 5322 regex (accepts .local / .corp TLDs).
  3. Must NOT be the same as the user's current email.
  4. Must NOT be already taken by another active user.
  5. Generates a cryptographically-secure 8-digit numeric verification code.
  6. Hashes the code with argon2id and stores in Redis (TTL 900 s / 15 min).
  7. Calls notification-service via TransactionalEmailSender to deliver the code.
  8. Emits auth.user.email_change_requested.v1 to the outbox.
  9. Returns masked_new_email — the raw code is NEVER returned to the caller.

Error cases:
  - EmailValidationError      (422)
  - EmailSameAsCurrentError   (422)
  - EmailAlreadyTakenError    (409)
  - EmailChannelNotConfiguredError (503) — propagated from sender
  - UserNotFoundError         (404)
"""

from __future__ import annotations

import re
import secrets
import uuid

import structlog

from auth_service.application.dto import (
    EmailChangeRequestedDTO,
    RequestEmailChangeCommand,
)
from auth_service.application.ports import (
    EventOutbox,
    PasswordHasher,
    RedisEmailChangeStore,
    TransactionalEmailSender,
    UserRepository,
)
from auth_service.domain.errors import (
    EmailAlreadyTakenError,
    EmailSameAsCurrentError,
    EmailValidationError,
    UserNotFoundError,
)
from auth_service.domain.events import UserEmailChangeRequested

log = structlog.get_logger(__name__)

# Permissive RFC 5322 regex — accepts .local / .corp TLDs.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

_CODE_DIGITS = 8
_TTL_SECONDS = 900  # 15 minutes
_MAX_ATTEMPTS = 3

_SUBJECT = "Лоцман — подтверждение смены email"
_BODY_TEMPLATE = (
    "Ваш код подтверждения смены email в системе Лоцман: {code}\n\n"
    "Это код подтверждения смены email в Лоцмане. "
    "Если вы не делали этот запрос — игнорируйте письмо.\n\n"
    "Код действителен 15 минут."
)


def _mask_email(email: str) -> str:
    """Mask an email for audit logging: 'FA_l***@dho***.ru' style."""
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[:3] + "***"

    parts = domain.rsplit(".", 1)
    if len(parts) == 2:
        domain_name, tld = parts
        masked_domain = domain_name[:3] + "***" if len(domain_name) > 3 else domain_name
        masked_domain = f"{masked_domain}.{tld}"
    else:
        masked_domain = domain[:3] + "***"

    return f"{masked_local}@{masked_domain}"


class RequestEmailChange:
    """Step 1 of self-service email change flow."""

    __slots__ = ("user_repo", "email_change_store", "hasher", "email_sender", "outbox")

    def __init__(
        self,
        *,
        user_repo: UserRepository,
        email_change_store: RedisEmailChangeStore,
        hasher: PasswordHasher,
        email_sender: TransactionalEmailSender,
        outbox: EventOutbox,
    ) -> None:
        self.user_repo = user_repo
        self.email_change_store = email_change_store
        self.hasher = hasher
        self.email_sender = email_sender
        self.outbox = outbox

    async def execute(self, *, cmd: RequestEmailChangeCommand) -> EmailChangeRequestedDTO:
        # Normalise
        new_email = cmd.new_email.lower().strip()

        # Validate format
        if not _EMAIL_RE.match(new_email):
            raise EmailValidationError("Invalid email address format")

        # Load actor
        user = await self.user_repo.get_by_id(cmd.actor_id)
        if user is None:
            raise UserNotFoundError()

        # Must not be same as current
        if new_email == user.email.lower():
            raise EmailSameAsCurrentError()

        # Must not be taken
        existing = await self.user_repo.get_by_email(new_email)
        if existing is not None and existing.is_active:
            raise EmailAlreadyTakenError()

        # Generate 8-digit code via cryptographically-secure source
        code = str(secrets.randbelow(10**_CODE_DIGITS)).zfill(_CODE_DIGITS)

        # Hash code for storage (re-use the password hasher — argon2id)
        code_hash = self.hasher.hash(code)

        # Store in Redis
        request_id = str(uuid.uuid4())
        await self.email_change_store.set_request(
            request_id,
            user_id=cmd.actor_id,
            new_email=new_email,
            code_hash=code_hash,
            attempts_remaining=_MAX_ATTEMPTS,
        )

        # Send email — may raise EmailChannelNotConfiguredError (propagated to caller)
        await self.email_sender.send(
            to=new_email,
            subject=_SUBJECT,
            body_text=_BODY_TEMPLATE.format(code=code),
        )

        # Emit audit event (code and raw new_email are NOT in the payload)
        masked = _mask_email(new_email)
        await self.outbox.publish(
            UserEmailChangeRequested(
                actor_id=cmd.actor_id,
                user_id=cmd.actor_id,
                masked_new_email=masked,
            ).as_envelope()
        )

        log.info(
            "email_change_requested",
            actor_id=str(cmd.actor_id),
            masked_new_email=masked,
            request_id=request_id,
        )

        return EmailChangeRequestedDTO(
            request_id=request_id,
            code_ttl_seconds=_TTL_SECONDS,
            masked_new_email=masked,
        )
