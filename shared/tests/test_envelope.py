# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for lotsman_shared.envelope."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lotsman_shared.envelope import EventEnvelope, make_envelope


def _actor() -> uuid.UUID:
    return uuid.uuid4()


def test_make_envelope_defaults() -> None:
    actor = _actor()
    env = make_envelope(
        event_type="registry.document.created.v1",
        actor_id=actor,
        payload={"document_id": "abc"},
    )
    assert isinstance(env.id, uuid.UUID)
    assert env.type == "registry.document.created.v1"
    assert env.actor_id == actor
    assert env.payload == {"document_id": "abc"}
    assert env.request_id is None
    assert env.version == 1
    assert env.occurred_at.tzinfo is not None


def test_make_envelope_explicit_id_and_time() -> None:
    fixed_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    fixed_time = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    actor = _actor()

    env = make_envelope(
        event_type="auth.user.created.v1",
        actor_id=actor,
        payload={},
        envelope_id=fixed_id,
        occurred_at=fixed_time,
        request_id="req-123",
    )

    assert env.id == fixed_id
    assert env.occurred_at == fixed_time
    assert env.request_id == "req-123"


def test_envelope_is_frozen() -> None:
    env = make_envelope(
        event_type="test.event.v1",
        actor_id=_actor(),
        payload={},
    )
    with pytest.raises((TypeError, ValidationError)):
        env.type = "mutated"  # type: ignore[misc]


def test_envelope_model_dump_json_serialisable() -> None:
    env = make_envelope(
        event_type="registry.asset.created.v1",
        actor_id=_actor(),
        payload={"name": "Test Corp"},
        request_id="req-abc",
    )
    dumped = env.model_dump(mode="json")
    assert isinstance(dumped["id"], str)
    assert isinstance(dumped["actor_id"], str)
    assert isinstance(dumped["occurred_at"], str)
    assert dumped["type"] == "registry.asset.created.v1"
    assert dumped["request_id"] == "req-abc"


def test_envelope_version_default() -> None:
    env = make_envelope(event_type="x.v1", actor_id=_actor(), payload={})
    assert env.version == 1


def test_envelope_custom_version() -> None:
    env = make_envelope(event_type="x.v2", actor_id=_actor(), payload={}, version=2)
    assert env.version == 2


def test_envelope_round_trip_via_model_validate() -> None:
    original = make_envelope(
        event_type="notification.delivery.sent.v1",
        actor_id=_actor(),
        payload={"channel": "email"},
        request_id="req-xyz",
    )
    dumped = original.model_dump(mode="json")
    restored = EventEnvelope.model_validate(dumped)
    assert restored.id == original.id
    assert restored.type == original.type
    assert restored.actor_id == original.actor_id
    assert restored.request_id == original.request_id
