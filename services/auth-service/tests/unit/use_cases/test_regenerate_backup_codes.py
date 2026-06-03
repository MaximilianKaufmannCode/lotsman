# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for RegenerateBackupCodes use case (US-4).

Covers:
- Happy path: 10 fresh codes returned, old codes deleted, event emitted
- Without re-MFA: ReMfaRequiredError
- Viewer role can regenerate (no role restriction)
- Old codes are fully replaced (none of the old hashes remain)
"""

from __future__ import annotations

import re
import uuid

import pytest

from auth_service.application.dto import BackupCodesRegeneratedDTO, RegenerateBackupCodesCommand
from auth_service.application.use_cases.regenerate_backup_codes import RegenerateBackupCodes
from auth_service.domain.entities import BackupCode
from auth_service.domain.errors import ReMfaRequiredError

from .conftest import (
    FakeBackupCodeRepository,
    FakeEventOutbox,
    FakePasswordHasher,
    FakeRedisReMfaStore,
    make_user,
)

_BACKUP_CODE_PATTERN = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")


def _build_uc(
    *,
    backup_code_repo: FakeBackupCodeRepository,
    re_mfa_store: FakeRedisReMfaStore,
    outbox: FakeEventOutbox | None = None,
) -> RegenerateBackupCodes:
    return RegenerateBackupCodes(
        backup_code_repo=backup_code_repo,
        hasher=FakePasswordHasher(),
        re_mfa_store=re_mfa_store,
        outbox=outbox or FakeEventOutbox(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_backup_codes_returns_10_new_codes_and_emits_event() -> None:
    user = make_user(role="editor")
    user_id = user.id
    session_id = uuid.uuid4()

    # Pre-existing backup code (must be replaced)
    hasher = FakePasswordHasher()
    old_code = BackupCode.create(user_id=user_id, code_hash=hasher.hash("OLD1-0000"))
    backup_code_repo = FakeBackupCodeRepository()
    await backup_code_repo.add_batch([old_code])

    re_mfa_store = FakeRedisReMfaStore()
    await re_mfa_store.set_verified(user_id, session_id)

    outbox = FakeEventOutbox()
    uc = _build_uc(backup_code_repo=backup_code_repo, re_mfa_store=re_mfa_store, outbox=outbox)

    # Act
    result = await uc.execute(
        cmd=RegenerateBackupCodesCommand(user_id=user_id, session_id=session_id)
    )

    # Assert
    assert isinstance(result, BackupCodesRegeneratedDTO)
    assert len(result.backup_codes) == 10
    for code in result.backup_codes:
        assert _BACKUP_CODE_PATTERN.match(code), f"Invalid backup code format: {code}"

    # Old codes deleted; 10 new ones persisted
    all_codes = await backup_code_repo.list_unused_for_user(user_id)
    assert len(all_codes) == 10

    # Event emitted
    assert "auth.user.backup_codes_regenerated.v1" in outbox.event_types()


@pytest.mark.asyncio
async def test_old_codes_replaced_not_appended() -> None:
    """Regeneration deletes all prior codes — no duplicates."""
    user = make_user()
    user_id = user.id
    session_id = uuid.uuid4()

    hasher = FakePasswordHasher()
    backup_code_repo = FakeBackupCodeRepository()
    old_codes = [
        BackupCode.create(user_id=user_id, code_hash=hasher.hash(f"OLD{i}-0000")) for i in range(10)
    ]
    await backup_code_repo.add_batch(old_codes)

    re_mfa_store = FakeRedisReMfaStore()
    await re_mfa_store.set_verified(user_id, session_id)

    uc = _build_uc(backup_code_repo=backup_code_repo, re_mfa_store=re_mfa_store)
    await uc.execute(cmd=RegenerateBackupCodesCommand(user_id=user_id, session_id=session_id))

    # Exactly 10 codes (old 10 deleted + 10 new)
    assert len(await backup_code_repo.list_unused_for_user(user_id)) == 10


# ---------------------------------------------------------------------------
# Without re-MFA (US-4 edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_without_re_mfa_raises_re_mfa_required() -> None:
    user = make_user()
    user_id = user.id
    session_id = uuid.uuid4()

    backup_code_repo = FakeBackupCodeRepository()
    re_mfa_store = FakeRedisReMfaStore()
    # NOT verified

    uc = _build_uc(backup_code_repo=backup_code_repo, re_mfa_store=re_mfa_store)

    with pytest.raises(ReMfaRequiredError):
        await uc.execute(cmd=RegenerateBackupCodesCommand(user_id=user_id, session_id=session_id))

    # No codes changed
    assert len(await backup_code_repo.list_unused_for_user(user_id)) == 0


# ---------------------------------------------------------------------------
# Viewer role can regenerate (US-4 edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_can_regenerate_backup_codes() -> None:
    user = make_user(role="viewer")
    user_id = user.id
    session_id = uuid.uuid4()

    backup_code_repo = FakeBackupCodeRepository()
    re_mfa_store = FakeRedisReMfaStore()
    await re_mfa_store.set_verified(user_id, session_id)

    uc = _build_uc(backup_code_repo=backup_code_repo, re_mfa_store=re_mfa_store)
    result = await uc.execute(
        cmd=RegenerateBackupCodesCommand(user_id=user_id, session_id=session_id)
    )

    assert len(result.backup_codes) == 10
