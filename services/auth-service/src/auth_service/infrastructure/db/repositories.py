# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Concrete async SQLAlchemy repository implementations for auth-service.

Each class implements the corresponding Protocol from application/ports.py.
All DB operations use the injected AsyncSession — callers own the transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from lotsman_shared.envelope import EventEnvelope
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.db.models import (
    LoginAttempt as LoginAttemptModel,
)
from auth_service.db.models import (
    Outbox as OutboxModel,
)
from auth_service.db.models import (
    Session as SessionModel,
)
from auth_service.db.models import (
    TotpBackupCode as TotpBackupCodeModel,
)
from auth_service.db.models import (
    TotpUsedCode as TotpUsedCodeModel,
)
from auth_service.db.models import (
    User as UserModel,
)
from auth_service.db.models import (
    UserSavedFilter as UserSavedFilterModel,
)
from auth_service.domain.entities import (
    BackupCode,
    LoginAttempt,
    SavedFilter,
    Session,
    TotpUsedCode,
    User,
)

# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _user_from_model(m: UserModel) -> User:
    return User(
        id=m.id,
        email=m.email,
        full_name=m.full_name,
        password_hash=m.password_hash,
        totp_secret_enc=m.totp_secret_enc,
        role=m.role,
        is_active=m.is_active,
        must_change_password=m.must_change_password,
        last_login_at=m.last_login_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
        deleted_at=m.deleted_at,
        ui_font_scale=m.ui_font_scale,
    )


def _session_from_model(m: SessionModel) -> Session:
    return Session(
        id=m.id,
        user_id=m.user_id,
        refresh_hash=m.refresh_hash,
        user_agent=m.user_agent,
        ip_address=m.ip_address,
        expires_at=m.expires_at,
        revoked_at=m.revoked_at,
        created_at=m.created_at,
    )


def _backup_code_from_model(m: TotpBackupCodeModel) -> BackupCode:
    return BackupCode(
        id=m.id,
        user_id=m.user_id,
        code_hash=m.code_hash,
        used_at=m.used_at,
        created_at=m.created_at,
    )


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------


class SqlaUserRepository:
    """Implements application.ports.UserRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.id == user_id))
        m = result.scalar_one_or_none()
        return _user_from_model(m) if m else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(
                func.lower(UserModel.email) == email.strip().lower(),
                UserModel.deleted_at.is_(None),
            )
        )
        m = result.scalar_one_or_none()
        return _user_from_model(m) if m else None

    async def add(self, user: User) -> None:
        m = UserModel(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            password_hash=user.password_hash,
            totp_secret_enc=user.totp_secret_enc,
            role=user.role,
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
            deleted_at=user.deleted_at,
            ui_font_scale=user.ui_font_scale,
        )
        self._session.add(m)

    async def update(self, user: User) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                email=user.email,
                full_name=user.full_name,
                password_hash=user.password_hash,
                totp_secret_enc=user.totp_secret_enc,
                role=user.role,
                is_active=user.is_active,
                must_change_password=user.must_change_password,
                last_login_at=user.last_login_at,
                updated_at=datetime.now(tz=UTC),
                deleted_at=user.deleted_at,
                ui_font_scale=user.ui_font_scale,
            )
        )

    async def count_active_admins(self) -> int:
        # FOR UPDATE serialises concurrent deactivate/demote transactions on the
        # admin rows, making the MIN_ADMINS invariant race-free (F-004 / ADR-0004 §6).
        return await self.count_active_by_role("admin")

    async def count_active_by_role(self, role: str) -> int:
        # FOR UPDATE serialises concurrent deactivate/demote transactions on the
        # target-role rows, making the MIN_* invariants race-free (F-004 / ADR-0004 §6).
        # NOTE: PostgreSQL forbids FOR UPDATE with aggregate functions, so we lock
        # the matching ROWS (SELECT id … FOR UPDATE) and count them in Python — this
        # both row-locks (race-free) and counts, unlike SELECT count(*) … FOR UPDATE
        # which raises FeatureNotSupportedError.
        result = await self._session.execute(
            select(UserModel.id)
            .where(
                UserModel.role == role,
                UserModel.is_active.is_(True),
                UserModel.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return len(result.scalars().all())

    async def list_all(self) -> list[User]:
        result = await self._session.execute(
            select(UserModel).where(UserModel.deleted_at.is_(None)).order_by(UserModel.created_at)
        )
        return [_user_from_model(m) for m in result.scalars()]


# ---------------------------------------------------------------------------
# SessionRepository
# ---------------------------------------------------------------------------


class SqlaSessionRepository:
    """Implements application.ports.SessionRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        result = await self._session.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        m = result.scalar_one_or_none()
        return _session_from_model(m) if m else None

    async def get_by_refresh_hash(self, refresh_hash: str) -> Session | None:
        result = await self._session.execute(
            select(SessionModel).where(SessionModel.refresh_hash == refresh_hash)
        )
        m = result.scalar_one_or_none()
        return _session_from_model(m) if m else None

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[Session]:
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            select(SessionModel).where(
                SessionModel.user_id == user_id,
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > now,
            )
        )
        return [_session_from_model(m) for m in result.scalars()]

    async def add(self, session: Session) -> None:
        m = SessionModel(
            id=session.id,
            user_id=session.user_id,
            refresh_hash=session.refresh_hash,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            expires_at=session.expires_at,
            revoked_at=session.revoked_at,
            created_at=session.created_at,
        )
        self._session.add(m)

    async def revoke(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id, SessionModel.revoked_at.is_(None))
            .values(revoked_at=datetime.now(tz=UTC))
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(SessionModel)
            .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return result.rowcount  # type: ignore[return-value]

    async def revoke_all_except(self, user_id: uuid.UUID, except_session_id: uuid.UUID) -> int:
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.id != except_session_id,
                SessionModel.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# LoginAttemptRepository
# ---------------------------------------------------------------------------


class SqlaLoginAttemptRepository:
    """Implements application.ports.LoginAttemptRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, attempt: LoginAttempt) -> None:
        m = LoginAttemptModel(
            id=attempt.id,
            email=attempt.email,
            ip_address=attempt.ip_address,
            outcome=attempt.outcome,
            user_agent=attempt.user_agent,
            created_at=attempt.created_at,
        )
        self._session.add(m)

    async def count_failures_since(self, email: str, since_seconds: int) -> int:
        from datetime import timedelta

        since = datetime.now(tz=UTC) - timedelta(seconds=since_seconds)
        result = await self._session.execute(
            select(func.count(LoginAttemptModel.id)).where(
                func.lower(LoginAttemptModel.email) == email.lower(),
                LoginAttemptModel.created_at >= since,
                LoginAttemptModel.outcome.in_(["failed_password", "failed_totp", "locked"]),
            )
        )
        return result.scalar_one()

    async def has_success_after_last_failure(self, email: str, window_seconds: int) -> bool:
        from datetime import timedelta

        since = datetime.now(tz=UTC) - timedelta(seconds=window_seconds)
        result = await self._session.execute(
            select(LoginAttemptModel)
            .where(
                func.lower(LoginAttemptModel.email) == email.lower(),
                LoginAttemptModel.created_at >= since,
                LoginAttemptModel.outcome == "success",
            )
            .order_by(LoginAttemptModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# BackupCodeRepository
# ---------------------------------------------------------------------------


class SqlaBackupCodeRepository:
    """Implements application.ports.BackupCodeRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_unused_for_user(self, user_id: uuid.UUID) -> list[BackupCode]:
        result = await self._session.execute(
            select(TotpBackupCodeModel).where(
                TotpBackupCodeModel.user_id == user_id,
                TotpBackupCodeModel.used_at.is_(None),
            )
        )
        return [_backup_code_from_model(m) for m in result.scalars()]

    async def count_unused_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count(TotpBackupCodeModel.id)).where(
                TotpBackupCodeModel.user_id == user_id,
                TotpBackupCodeModel.used_at.is_(None),
            )
        )
        return result.scalar_one()

    async def add_batch(self, codes: list[BackupCode]) -> None:
        for code in codes:
            m = TotpBackupCodeModel(
                id=code.id,
                user_id=code.user_id,
                code_hash=code.code_hash,
                used_at=code.used_at,
                created_at=code.created_at,
            )
            self._session.add(m)

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(TotpBackupCodeModel).where(TotpBackupCodeModel.user_id == user_id)
        )

    async def mark_used(self, code_id: uuid.UUID) -> None:
        await self._session.execute(
            update(TotpBackupCodeModel)
            .where(TotpBackupCodeModel.id == code_id)
            .values(used_at=datetime.now(tz=UTC))
        )


# ---------------------------------------------------------------------------
# TotpUsedCodeRepository
# ---------------------------------------------------------------------------


class SqlaTotpUsedCodeRepository:
    """Implements application.ports.TotpUsedCodeRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, user_id: uuid.UUID, period_index: int) -> bool:
        result = await self._session.execute(
            select(TotpUsedCodeModel).where(
                TotpUsedCodeModel.user_id == user_id,
                TotpUsedCodeModel.period_index == period_index,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, record: TotpUsedCode) -> None:
        # TotpUsedCodeModel has a composite PK (user_id, period_index) — no id column.
        # The domain entity carries a transient id UUID for identity; it is not persisted.
        m = TotpUsedCodeModel(
            user_id=record.user_id,
            period_index=record.period_index,
            used_at=record.used_at,
        )
        self._session.add(m)


# ---------------------------------------------------------------------------
# SavedFilterRepository
# ---------------------------------------------------------------------------


def _saved_filter_from_model(m: UserSavedFilterModel) -> SavedFilter:
    return SavedFilter(
        id=m.id,
        user_id=m.user_id,
        name=m.name,
        filter_json=m.filter_json,
        is_default=m.is_default,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlaSavedFilterRepository:
    """Implements application.ports.SavedFilterRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[SavedFilter]:
        result = await self._session.execute(
            select(UserSavedFilterModel)
            .where(UserSavedFilterModel.user_id == user_id)
            .order_by(
                UserSavedFilterModel.is_default.desc(),
                UserSavedFilterModel.name.asc(),
            )
        )
        return [_saved_filter_from_model(m) for m in result.scalars()]

    async def get_by_id(self, filter_id: uuid.UUID, user_id: uuid.UUID) -> SavedFilter | None:
        result = await self._session.execute(
            select(UserSavedFilterModel).where(
                UserSavedFilterModel.id == filter_id,
                UserSavedFilterModel.user_id == user_id,
            )
        )
        m = result.scalar_one_or_none()
        return _saved_filter_from_model(m) if m else None

    async def name_exists(self, user_id: uuid.UUID, name: str) -> bool:
        result = await self._session.execute(
            select(func.count(UserSavedFilterModel.id)).where(
                UserSavedFilterModel.user_id == user_id,
                UserSavedFilterModel.name == name,
            )
        )
        return result.scalar_one() > 0

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count(UserSavedFilterModel.id)).where(
                UserSavedFilterModel.user_id == user_id
            )
        )
        return result.scalar_one()

    async def add(self, saved_filter: SavedFilter) -> None:
        m = UserSavedFilterModel(
            id=saved_filter.id,
            user_id=saved_filter.user_id,
            name=saved_filter.name,
            filter_json=saved_filter.filter_json,
            is_default=saved_filter.is_default,
            created_at=saved_filter.created_at,
            updated_at=saved_filter.updated_at,
        )
        self._session.add(m)

    async def update(self, saved_filter: SavedFilter) -> None:
        await self._session.execute(
            update(UserSavedFilterModel)
            .where(UserSavedFilterModel.id == saved_filter.id)
            .values(
                name=saved_filter.name,
                filter_json=saved_filter.filter_json,
                is_default=saved_filter.is_default,
                updated_at=datetime.now(tz=UTC),
            )
        )

    async def delete(self, filter_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(UserSavedFilterModel).where(UserSavedFilterModel.id == filter_id)
        )

    async def unset_default_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(UserSavedFilterModel)
            .where(
                UserSavedFilterModel.user_id == user_id,
                UserSavedFilterModel.is_default.is_(True),
            )
            .values(is_default=False, updated_at=datetime.now(tz=UTC))
        )


# ---------------------------------------------------------------------------
# EventOutbox
# ---------------------------------------------------------------------------


class SqlaEventOutbox:
    """Implements application.ports.EventOutbox.

    Writes to auth.outbox in the SAME session/transaction as the business mutation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(self, envelope: EventEnvelope) -> None:
        m = OutboxModel(
            id=envelope.id,
            occurred_at=envelope.occurred_at,
            topic=f"auth.{envelope.type.split('.')[1]}",  # e.g. "auth.users"
            payload=envelope.model_dump(mode="json"),
        )
        self._session.add(m)
