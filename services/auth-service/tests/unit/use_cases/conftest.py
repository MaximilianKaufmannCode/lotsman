# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Shared in-memory fakes for use-case unit tests.

All fakes implement the port Protocols from application/ports.py.
No I/O — no Postgres, no Redis, no filesystem.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from lotsman_shared.envelope import EventEnvelope

from auth_service.domain.entities import (
    BackupCode,
    LoginAttempt,
    Session,
    TotpUsedCode,
    User,
)

# ---------------------------------------------------------------------------
# Fake repositories
# ---------------------------------------------------------------------------


class FakeUserRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, User] = {}

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._store.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        for u in self._store.values():
            if u.email.lower() == email.lower():
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


class FakeSessionRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, Session] = {}

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        return self._store.get(session_id)

    async def get_by_refresh_hash(self, refresh_hash: str) -> Session | None:
        for s in self._store.values():
            if s.refresh_hash == refresh_hash:
                return s
        return None

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[Session]:
        now = datetime.now(tz=UTC)
        return [
            s
            for s in self._store.values()
            if s.user_id == user_id and s.revoked_at is None and s.expires_at > now
        ]

    async def add(self, session: Session) -> None:
        self._store[session.id] = session

    async def revoke(self, session_id: uuid.UUID) -> None:
        s = self._store.get(session_id)
        if s and s.revoked_at is None:
            s.revoked_at = datetime.now(tz=UTC)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        count = 0
        now = datetime.now(tz=UTC)
        for s in self._store.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = now
                count += 1
        return count

    async def revoke_all_except(self, user_id: uuid.UUID, except_session_id: uuid.UUID) -> int:
        count = 0
        now = datetime.now(tz=UTC)
        for s in self._store.values():
            if s.user_id == user_id and s.id != except_session_id and s.revoked_at is None:
                s.revoked_at = now
                count += 1
        return count


class FakeLoginAttemptRepository:
    def __init__(self) -> None:
        self._store: list[LoginAttempt] = []

    async def add(self, attempt: LoginAttempt) -> None:
        self._store.append(attempt)

    async def count_failures_since(self, email: str, since_seconds: int) -> int:
        since = datetime.now(tz=UTC) - timedelta(seconds=since_seconds)
        failure_outcomes = {"failed_password", "failed_totp", "locked"}
        return sum(
            1
            for a in self._store
            if a.email.lower() == email.lower()
            and a.created_at >= since
            and a.outcome in failure_outcomes
        )

    async def has_success_after_last_failure(self, email: str, window_seconds: int) -> bool:
        since = datetime.now(tz=UTC) - timedelta(seconds=window_seconds)
        return any(
            a
            for a in self._store
            if a.email.lower() == email.lower() and a.created_at >= since and a.outcome == "success"
        )


class FakeBackupCodeRepository:
    def __init__(self) -> None:
        self._store: list[BackupCode] = []

    async def list_unused_for_user(self, user_id: uuid.UUID) -> list[BackupCode]:
        return [c for c in self._store if c.user_id == user_id and c.used_at is None]

    async def count_unused_for_user(self, user_id: uuid.UUID) -> int:
        return len([c for c in self._store if c.user_id == user_id and c.used_at is None])

    async def add_batch(self, codes: list[BackupCode]) -> None:
        self._store.extend(codes)

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        self._store = [c for c in self._store if c.user_id != user_id]

    async def mark_used(self, code_id: uuid.UUID) -> None:
        for c in self._store:
            if c.id == code_id:
                c.used_at = datetime.now(tz=UTC)


class FakeTotpUsedCodeRepository:
    def __init__(self) -> None:
        self._store: set[tuple[uuid.UUID, int]] = set()

    async def exists(self, user_id: uuid.UUID, period_index: int) -> bool:
        return (user_id, period_index) in self._store

    async def add(self, record: TotpUsedCode) -> None:
        self._store.add((record.user_id, record.period_index))


class FakeEventOutbox:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, envelope: EventEnvelope) -> None:
        self.events.append(envelope)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


# ---------------------------------------------------------------------------
# Fake service adapters
# ---------------------------------------------------------------------------


class FakePasswordHasher:
    """Stores plaintext as 'HASH:<password>' for test predictability."""

    def hash(self, password: str) -> str:
        return f"HASH:{password}"

    def verify(self, hash: str, password: str) -> bool:
        if hash == "SYSTEM":
            return False
        if not hash.startswith("HASH:"):
            return False
        return hash[5:] == password

    def check_needs_rehash(self, hash: str) -> bool:
        return False


class FakeTotpService:
    """Deterministic TOTP for tests — accepts any 6-digit code."""

    def __init__(self, *, always_valid: bool = True, period_index: int = 1000) -> None:
        self._always_valid = always_valid
        self._period_index = period_index

    def generate_secret_b32(self) -> str:
        return "JBSWY3DPEHPK3PXP"

    def make_otpauth_url(self, *, email: str, secret_b32: str, issuer: str) -> str:
        return f"otpauth://totp/{issuer}:{email}?secret={secret_b32}&issuer={issuer}"

    def verify(self, secret_b32: str, code: str, *, valid_window: int = 1) -> bool:
        return self._always_valid and len(code) == 6

    def current_period_index(self) -> int:
        return self._period_index


class FakeEncryptionService:
    """XOR-style pseudo-encryption for tests (identity for printable ASCII)."""

    def encrypt(self, plaintext: str) -> bytes:
        return b"ENC:" + plaintext.encode()

    def decrypt(self, ciphertext: bytes) -> str:
        if ciphertext.startswith(b"ENC:"):
            return ciphertext[4:].decode()
        raise ValueError("Invalid ciphertext")


class FakeJwtIssuer:
    def issue(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        role: str,
        session_id: uuid.UUID,
    ) -> str:
        return f"JWT:{user_id}:{role}"


class FakeBreachedPasswordChecker:
    def __init__(self, *, breach_words: frozenset[str] | None = None) -> None:
        self._breach = breach_words or frozenset()

    def is_breached(self, password: str) -> bool:
        return password in self._breach


# ---------------------------------------------------------------------------
# Fake Redis stores
# ---------------------------------------------------------------------------


class FakeRedisLockoutStore:
    def __init__(self) -> None:
        self._locked: set[uuid.UUID] = set()

    async def set_locked(self, user_id: uuid.UUID) -> None:
        self._locked.add(user_id)

    async def is_locked(self, user_id: uuid.UUID) -> bool:
        return user_id in self._locked

    async def remove_locked(self, user_id: uuid.UUID) -> None:
        self._locked.discard(user_id)


class FakeRedisTotpEnrollmentStore:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, str] = {}

    async def set_pending(self, user_id: uuid.UUID, secret_b32: str) -> None:
        self._store[user_id] = secret_b32

    async def get_pending(self, user_id: uuid.UUID) -> str | None:
        return self._store.get(user_id)

    async def delete_pending(self, user_id: uuid.UUID) -> None:
        self._store.pop(user_id, None)


class FakeRedisPendingTotpLoginStore:
    """In-memory fake for RedisPendingTotpLoginStore (ADR-0008 MF-1 compliant)."""

    def __init__(self) -> None:
        # Stores (user_id, scope_value) tuples keyed by ticket_id.
        self._store: dict[str, tuple[uuid.UUID, str]] = {}
        self._attempts: dict[str, int] = {}

    async def set_ticket(
        self,
        ticket_id: str,
        user_id: uuid.UUID,
        scope: object,  # TicketScope
    ) -> None:
        scope_val = scope.value if hasattr(scope, "value") else str(scope)
        self._store[ticket_id] = (user_id, scope_val)

    async def get_user_id(
        self,
        ticket_id: str,
        *,
        expected_scope: object,  # TicketScope
    ) -> uuid.UUID | None:
        entry = self._store.get(ticket_id)
        if entry is None:
            return None
        user_id, stored_scope = entry
        expected_val = (
            expected_scope.value if hasattr(expected_scope, "value") else str(expected_scope)
        )
        if stored_scope != expected_val:
            return None
        return user_id

    async def delete_ticket(self, ticket_id: str) -> None:
        self._store.pop(ticket_id, None)

    async def increment_confirm_attempts(self, ticket_id: str) -> int:
        self._attempts[ticket_id] = self._attempts.get(ticket_id, 0) + 1
        return self._attempts[ticket_id]

    async def delete_confirm_attempts(self, ticket_id: str) -> None:
        self._attempts.pop(ticket_id, None)


class FakeRedisReMfaStore:
    def __init__(self) -> None:
        self._store: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def set_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        self._store.add((user_id, session_id))

    async def is_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        return (user_id, session_id) in self._store


# ---------------------------------------------------------------------------
# Builder helper
# ---------------------------------------------------------------------------


def make_user(
    *,
    role: str = "editor",
    is_active: bool = True,
    must_change_password: bool = False,
    has_totp: bool = True,
    password: str = "secret",
    email: str = "user@example.com",
    now: datetime | None = None,
) -> User:
    from auth_service.domain.entities import TOTP_SENTINEL

    ts = now or datetime.now(tz=UTC)
    return User(
        id=uuid.uuid4(),
        email=email.lower(),
        full_name="Test User",
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


def make_session(
    *,
    user_id: uuid.UUID,
    refresh_hash: str = "abc123",
    ttl_days: int = 7,
    revoked: bool = False,
) -> Session:
    now = datetime.now(tz=UTC)
    s = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_hash=refresh_hash,
        user_agent="pytest",
        ip_address="127.0.0.1",
        expires_at=now + timedelta(days=ttl_days),
        revoked_at=datetime.now(tz=UTC) if revoked else None,
        created_at=now,
    )
    return s
