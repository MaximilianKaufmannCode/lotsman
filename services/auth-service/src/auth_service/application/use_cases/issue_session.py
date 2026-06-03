# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""IssueSession — shared session-minting collaborator (ADR-0008 D5.4.1 / MF-2).

Encapsulates the sequence that every terminal login step performs:
    1. Lockout re-check (CheckLockout).
    2. Create Session entity + persist via session_repo.
    3. Mint RS256 access JWT with the REAL ``sid = session.id`` (ADR-0003 §7).
    4. Set ``user.last_login_at = now(UTC)`` + persist via user_repo.
    5. Emit ``auth.user.logged_in.v1`` (LoggedIn) via outbox **in the caller's
       existing transaction** — the collaborator MUST NOT open or commit its own
       transaction.
    6. Record a SUCCESS login attempt (RecordLoginAttempt).
    7. Return the opaque refresh token (plaintext) + access JWT string.

Call sites:
  - VerifyTotp          (behaviour-preserving — already emitted LoggedIn/last_login_at)
  - ConfirmTotpEnrollment terminal branch  (enroll-only path, adds LoggedIn)
  - ChangePassword forced-enrollment path  (intentionally adds LoggedIn + last_login_at;
                                            that path emitted neither today — D5.4.1/D6)

The collaborator takes the ``user`` entity (already loaded by the caller) and the
``email`` / ``ip_address`` / ``user_agent`` context needed for session creation and
the login-attempt record.  It does NOT load the user itself — callers do that
so they can perform their own pre-checks before delegating here.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import LoginSuccessDTO, RecordLoginAttemptCommand
from auth_service.application.ports import (
    EventOutbox,
    JwtIssuer,
    LoginAttemptRepository,
    SessionRepository,
    UserRepository,
)
from auth_service.application.use_cases.check_lockout import CheckLockout
from auth_service.application.use_cases.record_login_attempt import RecordLoginAttempt
from auth_service.domain.entities import Session, User
from auth_service.domain.errors import InvalidCredentialsError
from auth_service.domain.events import LoggedIn
from auth_service.domain.value_objects import LoginOutcome

_DEFAULT_SESSION_TTL_SECONDS = 43200  # 12 hours


@dataclass(slots=True)
class IssueSession:
    """Session-minting collaborator shared by VerifyTotp, ConfirmTotpEnrollment,
    and ChangePassword forced-enrollment path (ADR-0008 MF-2 / D5.4).

    MUST be called inside an already-open ``async with db.begin()`` block.
    MUST NOT open or commit its own transaction.
    """

    user_repo: UserRepository
    session_repo: SessionRepository
    attempts_repo: LoginAttemptRepository
    jwt_issuer: JwtIssuer
    outbox: EventOutbox
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS

    async def execute(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
        method: str = "totp",
    ) -> LoginSuccessDTO:
        """Mint a session and return tokens.

        Steps (D5.4.5–D5.4.9):
          1. Lockout re-check — raises InvalidCredentialsError on locked.
          2. Session.create + session_repo.add.
          3. jwt_issuer.issue with sid = session.id (REAL sid — ADR-0003 §7).
          4. user.last_login_at = now(UTC) + user_repo.update.
          5. Emit LoggedIn event in the caller's transaction.
          6. Record SUCCESS login attempt.
          7. Return LoginSuccessDTO(access_token, refresh_token).
        """
        # Step 1: lockout re-check (D5.4.5)
        lockout = await CheckLockout(self.attempts_repo).execute(email=user.email)
        if lockout.is_locked:
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=user.email,
                    outcome=LoginOutcome.LOCKED,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
            raise InvalidCredentialsError()

        # Step 2: generate refresh token + create session (D5.4.6)
        refresh_token = secrets.token_urlsafe(32)
        refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        session = Session.create(
            user_id=user.id,
            refresh_hash=refresh_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            ttl_seconds=self.session_ttl_seconds,
        )
        await self.session_repo.add(session)

        # Step 3: mint RS256 access JWT with REAL sid = session.id (D5.4.7)
        access_token = self.jwt_issuer.issue(
            user_id=user.id,
            email=user.email,
            role=user.role,
            session_id=session.id,
        )

        # Step 4: set last_login_at + persist (D5.4.8)
        user.last_login_at = datetime.now(tz=UTC)
        await self.user_repo.update(user)

        # Step 5: emit LoggedIn in the caller's transaction (D5.4.8 / D6)
        event = LoggedIn(actor_id=user.id, session_id=session.id, method=method)
        await self.outbox.publish(event.as_envelope())

        # Step 6: record SUCCESS login attempt
        await RecordLoginAttempt(self.attempts_repo).execute(
            cmd=RecordLoginAttemptCommand(
                email=user.email,
                outcome=LoginOutcome.SUCCESS,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

        return LoginSuccessDTO(
            access_token=access_token,
            refresh_token=refresh_token,
        )
