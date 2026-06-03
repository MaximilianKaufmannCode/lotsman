# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RecordLoginAttempt use case — US-11/US-12 helper.

Writes a login_attempts row. Called in the same transaction as the login logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import RecordLoginAttemptCommand
from auth_service.application.ports import LoginAttemptRepository
from auth_service.domain.entities import LoginAttempt


@dataclass(slots=True)
class RecordLoginAttempt:
    attempts_repo: LoginAttemptRepository

    async def execute(self, *, cmd: RecordLoginAttemptCommand) -> None:
        attempt = LoginAttempt.create(
            email=cmd.email,
            outcome=cmd.outcome,
            ip_address=cmd.ip_address,
            user_agent=cmd.user_agent,
        )
        await self.attempts_repo.add(attempt)
