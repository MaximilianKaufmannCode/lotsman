# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: system-control domain whitelist."""

from system_control.domain.whitelist import (
    ALEMBIC_UPGRADE_CMD,
    ALLOWED_SERVICE_NAMES,
    ALLOWED_SERVICES,
    MAX_LOG_TAIL,
    SERVICE_TO_CONTAINER,
)


def test_allowed_service_names_is_frozenset() -> None:
    assert isinstance(ALLOWED_SERVICE_NAMES, frozenset)


def test_allowed_services_is_frozenset() -> None:
    assert isinstance(ALLOWED_SERVICES, frozenset)


def test_service_to_container_keys_match_allowed_names() -> None:
    assert set(SERVICE_TO_CONTAINER.keys()) == set(ALLOWED_SERVICE_NAMES)


def test_service_to_container_values_match_allowed_services() -> None:
    assert set(SERVICE_TO_CONTAINER.values()) == set(ALLOWED_SERVICES)


def test_all_container_names_have_lotsman_prefix() -> None:
    for name in ALLOWED_SERVICES:
        assert name.startswith("lotsman_"), f"{name!r} must start with 'lotsman_'"


def test_alembic_upgrade_cmd_is_correct() -> None:
    assert ALEMBIC_UPGRADE_CMD == ("alembic", "upgrade", "head")


def test_alembic_upgrade_cmd_is_tuple() -> None:
    # Tuples are immutable — safe to pass to exec_run without mutation risk.
    assert isinstance(ALEMBIC_UPGRADE_CMD, tuple)


def test_max_log_tail_is_bounded() -> None:
    assert MAX_LOG_TAIL == 500


def test_no_shell_metacharacters_in_container_names() -> None:
    """Container names must not contain shell metacharacters."""
    bad_chars = set(";&|$`\\\"'<>{}[]()#")
    for name in ALLOWED_SERVICES:
        for ch in bad_chars:
            assert ch not in name, f"Bad char {ch!r} in container name {name!r}"


def test_no_shell_metacharacters_in_service_names() -> None:
    bad_chars = set(";&|$`\\\"'<>{}[]()#")
    for name in ALLOWED_SERVICE_NAMES:
        for ch in bad_chars:
            assert ch not in name, f"Bad char {ch!r} in service name {name!r}"
