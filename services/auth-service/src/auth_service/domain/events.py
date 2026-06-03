# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain events for auth-service.

Each event corresponds to a state change and is published to the outbox
in the same transaction as the mutation that caused it.

Convention: event type strings follow ``auth.<noun>.<verb>.v1`` pattern.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from lotsman_shared.envelope import EventEnvelope, make_envelope


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# User lifecycle events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserCreated:
    event_type = "auth.user.created.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "email": self.email,
                "role": self.role,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserDeactivated:
    event_type = "auth.user.deactivated.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserActivated:
    event_type = "auth.user.activated.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserDeleted:
    event_type = "auth.user.deleted.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserRoleChanged:
    event_type = "auth.user.role_changed.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    before: str
    after: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "before": self.before,
                "after": self.after,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Profile events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserProfileUpdated:
    """Emitted when a user updates their own profile field (e.g. full_name).

    Mirrors UserRoleChanged shape: before/after values for the changed field.
    """

    event_type = "auth.user.profile_updated.v1"
    actor_id: uuid.UUID  # the user themselves
    user_id: uuid.UUID
    field: str
    before: str
    after: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "field": self.field,
                "before": self.before,
                "after": self.after,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Authentication events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoggedIn:
    event_type = "auth.user.logged_in.v1"
    actor_id: uuid.UUID
    session_id: uuid.UUID
    method: str = "totp"  # "totp" | "backup_code"
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"session_id": str(self.session_id), "method": self.method},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class LoggedOut:
    event_type = "auth.session.revoked.v1"
    actor_id: uuid.UUID
    session_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"session_id": str(self.session_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRevoked:
    event_type = "auth.session.revoked.v1"
    actor_id: uuid.UUID
    session_id: uuid.UUID
    target_user_id: uuid.UUID | None = None
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        payload: dict[str, Any] = {"session_id": str(self.session_id)}
        if self.target_user_id is not None:
            payload["target_user_id"] = str(self.target_user_id)
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload=payload,
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SessionRevokedAll:
    event_type = "auth.session.revoked_all.v1"
    actor_id: uuid.UUID
    target_user_id: uuid.UUID
    revoked_count: int
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "target_user_id": str(self.target_user_id),
                "revoked_count": self.revoked_count,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SessionRotated:
    event_type = "auth.session.rotated.v1"
    actor_id: uuid.UUID
    old_session_id: uuid.UUID
    new_session_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "old_session_id": str(self.old_session_id),
                "new_session_id": str(self.new_session_id),
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# SavedFilter events (v1.23.0 — registry-filters feature)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterPresetSaved:
    """Emitted when a user saves a new filter preset."""

    event_type = "auth.user.filter_preset_saved.v1"
    actor_id: uuid.UUID
    preset_id: uuid.UUID
    name: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "preset_id": str(self.preset_id),
                "name": self.name,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class FilterPresetUpdated:
    """Emitted when a user updates (renames or changes filter_json of) a preset."""

    event_type = "auth.user.filter_preset_updated.v1"
    actor_id: uuid.UUID
    preset_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"preset_id": str(self.preset_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class FilterPresetDeleted:
    """Emitted when a user deletes a filter preset."""

    event_type = "auth.user.filter_preset_deleted.v1"
    actor_id: uuid.UUID
    preset_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"preset_id": str(self.preset_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SessionReuseDetected:
    event_type = "auth.session.reuse_detected.v1"
    actor_id: uuid.UUID | None  # None if the token doesn't match any user
    severity: str = "HIGH"
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER

        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id or ACTOR_OUTBOX_DISPATCHER,
            payload={"severity": self.severity, "actor_id": str(self.actor_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# TOTP events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TotpEnrolled:
    event_type = "auth.user.totp_enrolled.v1"
    actor_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.actor_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class TotpReset:
    event_type = "auth.user.totp_reset.v1"
    actor_id: uuid.UUID  # admin performing the reset
    target_user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"target_user_id": str(self.target_user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class BackupCodesGenerated:
    event_type = "auth.user.backup_codes_regenerated.v1"
    actor_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.actor_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Password events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PasswordChanged:
    event_type = "auth.user.password_changed.v1"
    actor_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"user_id": str(self.actor_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasswordReset:
    event_type = "auth.user.password_reset.v1"
    actor_id: uuid.UUID  # admin
    target_user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "target_user_id": str(self.target_user_id),
                "severity": "info",
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Bootstrap events (ADR-0004 §3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserBootstrapped:
    """First-time bootstrap of an admin or super_admin user by SYSTEM_MIGRATOR via CLI.

    The ``role`` field distinguishes bootstrap_admin (role='admin') from
    bootstrap_super_admin (role='super_admin'). A single event class serves both
    bootstrap paths — no separate event class is needed.
    """

    event_type = "auth.user.bootstrapped.v1"
    actor_id: uuid.UUID  # always ACTOR_SYSTEM_MIGRATOR
    user_id: uuid.UUID
    email: str
    role: str = "admin"  # 'admin' | 'super_admin'
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "email": self.email,
                "role": self.role,
                # OTP is intentionally NOT included in the audit payload
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class InvitationResent:
    """OTP rotated for a TOTP-less user (re-bootstrap or re-invite)."""

    event_type = "auth.invitation.resent.v1"
    actor_id: uuid.UUID  # ACTOR_SYSTEM_MIGRATOR for CLI re-bootstrap
    user_id: uuid.UUID
    email: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "email": self.email,
                # OTP is intentionally NOT included in the audit payload
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Invite events (ADR-0004 Phase 2b)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserInvited:
    """Admin invited a new user (delivery=auto or show-otp).

    Carries delivery mode and channel_used (None for show-otp).
    """

    event_type = "auth.user.invited.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    delivery: str  # "auto" | "show-otp"
    channel_used: str | None  # "email" | "telegram" | "dion" | None
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "email": self.email,
                "role": self.role,
                "delivery": self.delivery,
                "channel_used": self.channel_used,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class InviteRequested:
    """Notification-service should deliver the OTP via the given channel.

    Published to the auth.outbox. notification-service consumes this via
    Redis Stream (consumer wiring is Phase 4).
    """

    event_type = "notification.invite.requested.v1"
    actor_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    otp: str  # plaintext OTP — NOT in audit log
    login_url: str
    channel_preference: str
    ttl_minutes: int = 10
    invitation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        # OTP intentionally NOT in audit payload (see ADR-0004 §6, F-001 in admin-channels-review).
        # The OTP is delivered side-channel: invite_user / re_invite_user write it to
        # Redis under key invite:otp:{invitation_id} (TTL=600 s) via InviteOtpPublisher.
        # The notification-service consumer reads and immediately deletes that key.
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "email": self.email,
                "role": self.role,
                "login_url": self.login_url,
                "channel_preference": self.channel_preference,
                "ttl_minutes": self.ttl_minutes,
                "invitation_id": str(self.invitation_id),
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Email-change events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserEmailChangeRequested:
    """Emitted when a user requests a self-service email change.

    The raw new_email is NOT included — only the masked form to prevent
    full email addresses from persisting in the audit log at request-time.
    The full emails are included in UserEmailChanged (the confirmation event).
    """

    event_type = "auth.user.email_change_requested.v1"
    actor_id: uuid.UUID  # the user themselves
    user_id: uuid.UUID
    masked_new_email: str
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "masked_new_email": self.masked_new_email,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserEmailChanged:
    """Emitted when the email change is confirmed and the email is updated in the DB.

    Full before/after emails are included — they are identifiers, not secrets.
    This event is the source of truth for the audit log (who changed to what).
    """

    event_type = "auth.user.email_changed.v1"
    actor_id: uuid.UUID  # the user themselves
    user_id: uuid.UUID
    before: str  # old email
    after: str  # new email
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "user_id": str(self.user_id),
                "before": self.before,
                "after": self.after,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Policy violation events (ADR-0004 §6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyViolationAttempted:
    """An admin operation was blocked by a policy (e.g. MIN_ADMINS)."""

    event_type = "auth.policy.violation.v1"
    actor_id: uuid.UUID
    policy: str  # "MIN_ADMINS"
    target_user_id: uuid.UUID | None = None
    operation: str | None = None  # "deactivate" | "role_change"
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        payload: dict[str, Any] = {"policy": self.policy}
        if self.target_user_id is not None:
            payload["target_user_id"] = str(self.target_user_id)
        if self.operation is not None:
            payload["operation"] = self.operation
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload=payload,
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Lockout events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountLocked:
    event_type = "auth.account.locked.v1"
    actor_id: uuid.UUID  # ACTOR_OUTBOX_DISPATCHER for system-triggered lockouts
    target_email: str
    duration_hours: int
    severity: str = "HIGH"
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={
                "target_email": self.target_email,
                "duration_hours": self.duration_hours,
                "severity": self.severity,
            },
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserLocked:
    """Admin instant lockout (Redis flag)."""

    event_type = "auth.user.locked.v1"
    actor_id: uuid.UUID  # admin
    target_user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"target_user_id": str(self.target_user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserUnlocked:
    """Admin removes instant lockout flag."""

    event_type = "auth.account.unlocked.v1"
    actor_id: uuid.UUID  # admin
    target_user_id: uuid.UUID
    occurred_at: datetime = field(default_factory=_now)

    def as_envelope(self, *, request_id: str | None = None) -> EventEnvelope:
        return make_envelope(
            event_type=self.event_type,
            actor_id=self.actor_id,
            payload={"target_user_id": str(self.target_user_id)},
            occurred_at=self.occurred_at,
            request_id=request_id,
        )
