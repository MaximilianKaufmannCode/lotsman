# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""System actor UUID constants for Лоцман.

Single source of truth per ADR-0002 acceptance decision #2 and
docs/db/system-actors.md. These values are pinned — never regenerated.

Services import these constants from lotsman_shared.actors; they must
never hardcode the UUIDs inline.

The same UUIDs are inserted into auth.users (is_active=false, password_hash='SYSTEM')
by infra/postgres/init/02-system-actors.sql and alembic migration 0002_seed_system_actors.
"""

from __future__ import annotations

import uuid
from enum import Enum

# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

ACTOR_OUTBOX_DISPATCHER: uuid.UUID = uuid.UUID("018f4e2a-dead-7000-8000-000000000001")
"""ARQ worker that polls <schema>.outbox and XADD-s to Redis Streams."""

ACTOR_NOTIFICATION_SCHEDULER: uuid.UUID = uuid.UUID("018f4e2a-dead-7000-8000-000000000002")
"""ARQ worker that computes and enqueues delivery attempts."""

ACTOR_AUDIT_RECORDER: uuid.UUID = uuid.UUID("018f4e2a-dead-7000-8000-000000000003")
"""ARQ consumer that writes rows to audit.events."""

ACTOR_SYSTEM_MIGRATOR: uuid.UUID = uuid.UUID("018f4e2a-dead-7000-8000-000000000004")
"""Identity used in audit rows produced during Alembic migrations."""

ACTOR_SEED_LOADER: uuid.UUID = uuid.UUID("018f4e2a-dead-7000-8000-000000000005")
"""Identity used during 'make seed' (reference data load)."""

# ---------------------------------------------------------------------------
# Enum for iteration and display
# ---------------------------------------------------------------------------

_ACTOR_MAP: dict[str, uuid.UUID] = {
    "OUTBOX_DISPATCHER": ACTOR_OUTBOX_DISPATCHER,
    "NOTIFICATION_SCHEDULER": ACTOR_NOTIFICATION_SCHEDULER,
    "AUDIT_RECORDER": ACTOR_AUDIT_RECORDER,
    "SYSTEM_MIGRATOR": ACTOR_SYSTEM_MIGRATOR,
    "SEED_LOADER": ACTOR_SEED_LOADER,
}


class SystemActor(Enum):
    """Enumeration of all reserved system actors.

    Usage::

        from lotsman_shared.actors import SystemActor
        for actor in SystemActor:
            print(actor.name, actor.value)
    """

    OUTBOX_DISPATCHER = ACTOR_OUTBOX_DISPATCHER
    NOTIFICATION_SCHEDULER = ACTOR_NOTIFICATION_SCHEDULER
    AUDIT_RECORDER = ACTOR_AUDIT_RECORDER
    SYSTEM_MIGRATOR = ACTOR_SYSTEM_MIGRATOR
    SEED_LOADER = ACTOR_SEED_LOADER

    @classmethod
    def by_uuid(cls, uid: uuid.UUID) -> SystemActor | None:
        """Return the matching SystemActor or None if the UUID is not a system actor."""
        for member in cls:
            if member.value == uid:
                return member
        return None

    def is_system_actor(self, uid: uuid.UUID) -> bool:
        """Return True if uid matches this actor's UUID."""
        return self.value == uid


def is_system_actor(uid: uuid.UUID) -> bool:
    """Return True if uid corresponds to any reserved system actor."""
    return SystemActor.by_uuid(uid) is not None
