# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for BootstrapSuperAdmin use case — ADR-0006 Phase 1.

Mirrors test_bootstrap_admin.py but for role=super_admin.

Covers 3 Gherkin scenarios:

  Scenario 1: Happy path — fresh email creates user, emits bootstrapped event with
              role=super_admin, OTP in Redis.
  Scenario 2: Idempotent — second call rotates OTP, emits invitation.resent, old OTP gone.
  Scenario 3: Blocked — user with totp_secret_enc set returns non-zero exit, no changes.

These tests use in-memory fakes so they run without Docker.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR
from lotsman_shared.envelope import EventEnvelope

from auth_service.application.dto import BootstrapSuperAdminCommand
from auth_service.application.use_cases.bootstrap_super_admin import (
    BootstrapSuperAdmin,
    UserHasActiveTotpError,
    _generate_otp,
)
from auth_service.domain.entities import TOTP_SENTINEL, User

# ---------------------------------------------------------------------------
# Inline in-memory fakes (avoid cross-package imports from unit/ conftest)
# ---------------------------------------------------------------------------


class FakeUserRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, User] = {}

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._store.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        for u in self._store.values():
            if u.email.lower() == email.lower() and u.deleted_at is None:
                return u
        return None

    async def add(self, user: User) -> None:
        self._store[user.id] = user

    async def update(self, user: User) -> None:
        self._store[user.id] = user

    async def count_active_admins(self) -> int:
        return await self.count_active_by_role("admin")

    async def count_active_by_role(self, role: str) -> int:
        return sum(
            1
            for u in self._store.values()
            if u.role == role and u.is_active and u.deleted_at is None
        )

    async def list_all(self) -> list[User]:
        return [u for u in self._store.values() if u.deleted_at is None]


class FakePasswordHasher:
    """Stores plaintext as 'HASH:<password>' for test predictability."""

    def hash(self, password: str) -> str:
        return f"HASH:{password}"

    def verify(self, hash: str, password: str) -> bool:
        if hash == "SYSTEM" or not hash.startswith("HASH:"):
            return False
        return hash[5:] == password

    def check_needs_rehash(self, hash: str) -> bool:
        return False


class FakeEventOutbox:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, envelope: EventEnvelope) -> None:
        self.events.append(envelope)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


def make_user(
    *,
    role: str = "super_admin",
    is_active: bool = True,
    must_change_password: bool = False,
    has_totp: bool = True,
    password: str = "secret",
    email: str = "super@example.com",
) -> User:
    ts = datetime.now(tz=UTC)
    return User(
        id=uuid.uuid4(),
        email=email.lower(),
        full_name="Test Super Admin",
        password_hash=f"HASH:{password}",
        totp_secret_enc=b"ENC:JBSWY3DPEHPK3PXP" if has_totp else TOTP_SENTINEL,
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
        last_login_at=None,
        created_at=ts,
        updated_at=ts,
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# Fake bootstrap OTP store (in-memory, simulates Redis key lifecycle)
# ---------------------------------------------------------------------------


class FakeBootstrapOtpStore:
    """In-memory implementation of RedisBootstrapOtpStore for testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set_otp(self, email: str, otp_hash: str) -> None:
        self._store[email.strip().lower()] = otp_hash

    async def get_otp_hash(self, email: str) -> str | None:
        return self._store.get(email.strip().lower())

    async def delete_otp(self, email: str) -> None:
        self._store.pop(email.strip().lower(), None)

    def has_key(self, email: str) -> bool:
        return email.strip().lower() in self._store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture()
def hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture()
def otp_store() -> FakeBootstrapOtpStore:
    return FakeBootstrapOtpStore()


@pytest.fixture()
def outbox() -> FakeEventOutbox:
    return FakeEventOutbox()


@pytest.fixture()
def use_case(
    user_repo: FakeUserRepository,
    hasher: FakePasswordHasher,
    otp_store: FakeBootstrapOtpStore,
    outbox: FakeEventOutbox,
) -> BootstrapSuperAdmin:
    return BootstrapSuperAdmin(
        user_repo=user_repo,
        hasher=hasher,
        otp_store=otp_store,
        outbox=outbox,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_OTP_PATTERN = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def _cmd(
    email: str = "super@org.local", full_name: str = "Надежда Суперова"
) -> BootstrapSuperAdminCommand:
    return BootstrapSuperAdminCommand(email=email, full_name=full_name)


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — bootstrap on empty instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario1_happy_path_fresh_email(
    use_case: BootstrapSuperAdmin,
    user_repo: FakeUserRepository,
    otp_store: FakeBootstrapOtpStore,
    outbox: FakeEventOutbox,
) -> None:
    """Given no user with email=super@org.local:
    - no exception raised
    - OTP matches XXXX-XXXX-XXXX pattern
    - auth.users row created with role=super_admin, is_active=True, must_change_password=True
    - Redis key 'bootstrap:otp:super@org.local' exists
    - exactly one auth.user.bootstrapped.v1 event with actor_id=SYSTEM_MIGRATOR
    - event payload.role == 'super_admin'
    - OTP value does NOT appear in event payloads
    """
    cmd = _cmd()
    result = await use_case.execute(cmd=cmd)

    # OTP format matches spec
    assert _OTP_PATTERN.match(result.oob_otp), (
        f"OTP {result.oob_otp!r} does not match XXXX-XXXX-XXXX"
    )

    # User created in repo
    stored = await user_repo.get_by_id(result.user_id)
    assert stored is not None
    assert stored.email == "super@org.local"
    assert stored.role == "super_admin"
    assert stored.is_active is True
    assert stored.must_change_password is True
    assert stored.totp_secret_enc == TOTP_SENTINEL, (
        "totp_secret_enc must be SENTINEL (NULL equivalent)"
    )

    # Redis key exists
    assert otp_store.has_key("super@org.local"), "bootstrap:otp:super@org.local must exist in Redis"

    # Exactly one bootstrapped event emitted
    assert outbox.event_types().count("auth.user.bootstrapped.v1") == 1

    # Audit event actor is SYSTEM_MIGRATOR
    bootstrap_event = next(e for e in outbox.events if e.type == "auth.user.bootstrapped.v1")
    assert bootstrap_event.actor_id == ACTOR_SYSTEM_MIGRATOR

    # Event payload role is super_admin
    assert bootstrap_event.payload["role"] == "super_admin"

    # OTP must NOT appear in event payload
    payload_str = str(bootstrap_event.payload)
    assert result.oob_otp not in payload_str, "OTP must not appear in audit event payload"


# ---------------------------------------------------------------------------
# Scenario 2: Idempotent re-bootstrap — user exists without TOTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario2_idempotent_rebootstrap_rotates_otp(
    use_case: BootstrapSuperAdmin,
    user_repo: FakeUserRepository,
    otp_store: FakeBootstrapOtpStore,
    outbox: FakeEventOutbox,
    hasher: FakePasswordHasher,
) -> None:
    """Given auth.users contains email=super@org.local with totp_secret_enc=NULL:
    - second call exits with code 0 equivalent (no exception)
    - returns a NEW OTP distinct from the old one
    - Redis key now contains NEW OTP hash, old OTP hash is gone
    - auth.users row is updated (password_hash changes), must_change_password=True
    - audit.events emits auth.invitation.resent.v1 (not bootstrapped again)
    """
    # Pre-populate a TOTP-less super_admin user + set an "old" OTP in the store
    existing_user = make_user(
        email="super@org.local",
        role="super_admin",
        has_totp=False,
        password="old-otp-hash",
        must_change_password=True,
    )
    await user_repo.add(existing_user)
    old_otp = "AAAA-BBBB-CCCC"
    old_hash = hasher.hash(old_otp)
    await otp_store.set_otp("super@org.local", old_hash)

    # Execute second bootstrap
    cmd = _cmd()
    result = await use_case.execute(cmd=cmd)

    # Returns a new OTP (distinct from old)
    assert result.oob_otp != old_otp
    assert _OTP_PATTERN.match(result.oob_otp)

    # Old OTP hash is gone from Redis, new hash is present
    new_stored_hash = await otp_store.get_otp_hash("super@org.local")
    assert new_stored_hash is not None
    assert new_stored_hash != old_hash, "Redis must contain new OTP hash, not old"

    # auth.users row unchanged except password_hash and updated_at
    updated = await user_repo.get_by_id(existing_user.id)
    assert updated is not None
    assert updated.email == existing_user.email
    assert updated.role == "super_admin"
    assert updated.is_active is True
    assert updated.must_change_password is True
    assert updated.totp_secret_enc == TOTP_SENTINEL

    # Emits invitation.resent, NOT bootstrapped again
    assert "auth.invitation.resent.v1" in outbox.event_types()
    assert "auth.user.bootstrapped.v1" not in outbox.event_types()

    # Resent event actor is SYSTEM_MIGRATOR
    resent_event = next(e for e in outbox.events if e.type == "auth.invitation.resent.v1")
    assert resent_event.actor_id == ACTOR_SYSTEM_MIGRATOR

    # New OTP must not appear in event payload
    payload_str = str(resent_event.payload)
    assert result.oob_otp not in payload_str, "New OTP must not appear in audit event payload"


# ---------------------------------------------------------------------------
# Scenario 3: Blocked — user has active TOTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario3_blocked_when_user_has_active_totp(
    use_case: BootstrapSuperAdmin,
    user_repo: FakeUserRepository,
    otp_store: FakeBootstrapOtpStore,
    outbox: FakeEventOutbox,
) -> None:
    """Given auth.users contains email=active-super@org.local with totp_secret_enc IS NOT NULL:
    - raises UserHasActiveTotpError (CLI exits non-zero)
    - no DB changes (no user modified)
    - no event emitted
    """
    # Pre-populate a fully enrolled super_admin
    active_user = make_user(
        email="active-super@org.local",
        role="super_admin",
        has_totp=True,  # totp_secret_enc != TOTP_SENTINEL
        password="current-password-hash",
    )
    original_password_hash = active_user.password_hash
    await user_repo.add(active_user)

    cmd = BootstrapSuperAdminCommand(email="active-super@org.local", full_name="Some Name")

    with pytest.raises(UserHasActiveTotpError) as exc_info:
        await use_case.execute(cmd=cmd)

    # Error message contains the mandatory text from spec
    assert "user has active TOTP" in exc_info.value.default_message
    assert "super-admin-runbook" in exc_info.value.default_message

    # No DB changes
    unchanged = await user_repo.get_by_id(active_user.id)
    assert unchanged is not None
    assert unchanged.password_hash == original_password_hash

    # No events emitted
    assert outbox.events == []

    # No OTP written to Redis
    assert not otp_store.has_key("active-super@org.local")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_is_case_normalised(
    use_case: BootstrapSuperAdmin,
    user_repo: FakeUserRepository,
    otp_store: FakeBootstrapOtpStore,
) -> None:
    """Email is normalised to lowercase before storage."""
    cmd = BootstrapSuperAdminCommand(email="SUPER@ORG.LOCAL", full_name="Test")
    result = await use_case.execute(cmd=cmd)

    stored = await user_repo.get_by_id(result.user_id)
    assert stored is not None
    assert stored.email == "super@org.local"
    assert otp_store.has_key("super@org.local")


@pytest.mark.asyncio
async def test_user_created_with_super_admin_role(
    use_case: BootstrapSuperAdmin,
    user_repo: FakeUserRepository,
) -> None:
    """Bootstrap always creates with role=super_admin regardless of any default."""
    cmd = _cmd()
    result = await use_case.execute(cmd=cmd)
    stored = await user_repo.get_by_id(result.user_id)
    assert stored is not None
    assert stored.role == "super_admin"


# ---------------------------------------------------------------------------
# Unit tests for OTP format helper
# ---------------------------------------------------------------------------


def test_generate_otp_matches_pattern() -> None:
    """_generate_otp() must always produce XXXX-XXXX-XXXX format."""
    for _ in range(20):
        otp = _generate_otp()
        assert _OTP_PATTERN.match(otp), f"Generated OTP {otp!r} does not match pattern"


def test_generate_otp_is_random() -> None:
    """Two consecutive calls must not produce the same OTP."""
    otp1 = _generate_otp()
    otp2 = _generate_otp()
    assert otp1 != otp2, "OTPs must be distinct (randomness check)"
