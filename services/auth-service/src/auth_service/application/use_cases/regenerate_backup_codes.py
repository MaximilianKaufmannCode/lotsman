# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RegenerateBackupCodes use case — US-4.

Requires re-MFA (Redis mfa-verified flag, 5-minute TTL).
Deletes all existing backup codes and generates 10 new single-use argon2id-hashed codes.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from auth_service.application.dto import BackupCodesRegeneratedDTO, RegenerateBackupCodesCommand
from auth_service.application.ports import (
    BackupCodeRepository,
    EventOutbox,
    PasswordHasher,
    RedisReMfaStore,
)
from auth_service.domain.entities import BackupCode
from auth_service.domain.errors import ReMfaRequiredError
from auth_service.domain.events import BackupCodesGenerated
from auth_service.domain.value_objects import BackupCodeFormat

_BACKUP_CODE_COUNT = 10


@dataclass(slots=True)
class RegenerateBackupCodes:
    backup_code_repo: BackupCodeRepository
    hasher: PasswordHasher
    re_mfa_store: RedisReMfaStore
    outbox: EventOutbox

    async def execute(self, *, cmd: RegenerateBackupCodesCommand) -> BackupCodesRegeneratedDTO:
        # 1. Re-MFA gate (US-4 edge case)
        verified = await self.re_mfa_store.is_verified(cmd.user_id, cmd.session_id)
        if not verified:
            raise ReMfaRequiredError()

        # 2. Delete old codes
        await self.backup_code_repo.delete_all_for_user(cmd.user_id)

        # 3. Generate 10 new codes
        plaintext_codes: list[str] = []
        new_codes: list[BackupCode] = []
        for _ in range(_BACKUP_CODE_COUNT):
            code_bytes = secrets.token_bytes(4)
            code_str = BackupCodeFormat.generate(code_bytes)
            code_hash = self.hasher.hash(code_str)
            plaintext_codes.append(code_str)
            new_codes.append(BackupCode.create(user_id=cmd.user_id, code_hash=code_hash))

        await self.backup_code_repo.add_batch(new_codes)

        # 4. Emit event
        await self.outbox.publish(BackupCodesGenerated(actor_id=cmd.user_id).as_envelope())

        return BackupCodesRegeneratedDTO(backup_codes=plaintext_codes)
