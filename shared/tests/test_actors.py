# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for lotsman_shared.actors."""

from __future__ import annotations

import uuid

from lotsman_shared.actors import (
    ACTOR_AUDIT_RECORDER,
    ACTOR_NOTIFICATION_SCHEDULER,
    ACTOR_OUTBOX_DISPATCHER,
    ACTOR_SEED_LOADER,
    ACTOR_SYSTEM_MIGRATOR,
    SystemActor,
    is_system_actor,
)


def test_constants_are_uuid_instances() -> None:
    for const in [
        ACTOR_OUTBOX_DISPATCHER,
        ACTOR_NOTIFICATION_SCHEDULER,
        ACTOR_AUDIT_RECORDER,
        ACTOR_SYSTEM_MIGRATOR,
        ACTOR_SEED_LOADER,
    ]:
        assert isinstance(const, uuid.UUID)


def test_constants_match_docs_db_system_actors() -> None:
    """Exact UUIDs from docs/db/system-actors.md — must never drift."""
    assert str(ACTOR_OUTBOX_DISPATCHER) == "018f4e2a-dead-7000-8000-000000000001"
    assert str(ACTOR_NOTIFICATION_SCHEDULER) == "018f4e2a-dead-7000-8000-000000000002"
    assert str(ACTOR_AUDIT_RECORDER) == "018f4e2a-dead-7000-8000-000000000003"
    assert str(ACTOR_SYSTEM_MIGRATOR) == "018f4e2a-dead-7000-8000-000000000004"
    assert str(ACTOR_SEED_LOADER) == "018f4e2a-dead-7000-8000-000000000005"


def test_system_actor_enum_has_five_members() -> None:
    assert len(list(SystemActor)) == 5


def test_system_actor_enum_values_match_constants() -> None:
    assert SystemActor.OUTBOX_DISPATCHER.value == ACTOR_OUTBOX_DISPATCHER
    assert SystemActor.NOTIFICATION_SCHEDULER.value == ACTOR_NOTIFICATION_SCHEDULER
    assert SystemActor.AUDIT_RECORDER.value == ACTOR_AUDIT_RECORDER
    assert SystemActor.SYSTEM_MIGRATOR.value == ACTOR_SYSTEM_MIGRATOR
    assert SystemActor.SEED_LOADER.value == ACTOR_SEED_LOADER


def test_by_uuid_returns_matching_member() -> None:
    assert SystemActor.by_uuid(ACTOR_OUTBOX_DISPATCHER) is SystemActor.OUTBOX_DISPATCHER
    assert SystemActor.by_uuid(ACTOR_AUDIT_RECORDER) is SystemActor.AUDIT_RECORDER


def test_by_uuid_returns_none_for_unknown() -> None:
    random_uuid = uuid.uuid4()
    assert SystemActor.by_uuid(random_uuid) is None


def test_is_system_actor_true_for_reserved() -> None:
    assert is_system_actor(ACTOR_OUTBOX_DISPATCHER) is True
    assert is_system_actor(ACTOR_SEED_LOADER) is True


def test_is_system_actor_false_for_user_uuid() -> None:
    assert is_system_actor(uuid.uuid4()) is False


def test_constants_are_all_distinct() -> None:
    constants = [
        ACTOR_OUTBOX_DISPATCHER,
        ACTOR_NOTIFICATION_SCHEDULER,
        ACTOR_AUDIT_RECORDER,
        ACTOR_SYSTEM_MIGRATOR,
        ACTOR_SEED_LOADER,
    ]
    assert len(set(constants)) == len(constants)
