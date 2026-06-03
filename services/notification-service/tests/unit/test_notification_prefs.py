# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for the notification-preferences domain logic (ADR-0011 §D2)."""

from __future__ import annotations

from dataclasses import dataclass

from notification_service.domain.notification_prefs import (
    DEFAULT_CATEGORIES,
    effective,
    sanitize_categories,
    wants,
)


@dataclass
class _Row:
    enabled: bool = True
    suppress_own: bool = True
    email_mode: str = "digest"
    categories: dict | None = None


def test_effective_none_returns_defaults() -> None:
    eff = effective(None)
    assert eff.enabled is True
    assert eff.suppress_own is True
    assert eff.email_mode == "digest"
    assert eff.categories == DEFAULT_CATEGORIES
    # must be a copy, not the shared module constant
    eff.categories["deadline"]["email"] = False
    assert DEFAULT_CATEGORIES["deadline"]["email"] is True


def test_effective_merges_partial_row_over_defaults() -> None:
    row = _Row(categories={"deadline": {"email": False}})  # only one key set
    eff = effective(row)
    # explicit override wins
    assert eff.categories["deadline"]["email"] is False
    # missing in_app falls back to default (True)
    assert eff.categories["deadline"]["in_app"] is True
    # untouched categories keep defaults
    assert eff.categories["doc_created"] == DEFAULT_CATEGORIES["doc_created"]


def test_effective_invalid_email_mode_falls_back() -> None:
    assert effective(_Row(email_mode="weird")).email_mode == "digest"
    assert effective(_Row(email_mode="off")).email_mode == "off"


def test_wants_default_user_gets_deadline_email() -> None:
    assert wants(None, "deadline", "email") is True


def test_wants_master_switch_off_silences_everything() -> None:
    row = _Row(enabled=False)
    assert wants(row, "deadline", "email") is False
    assert wants(row, "doc_assigned", "in_app") is False


def test_wants_respects_category_optout() -> None:
    row = _Row(categories={"deadline": {"email": False}})
    assert wants(row, "deadline", "email") is False
    assert wants(row, "deadline", "in_app") is True  # in_app default still on


def test_wants_unknown_category_or_channel_is_false() -> None:
    assert wants(None, "nope", "email") is False
    assert wants(None, "deadline", "telegram") is False


def test_sanitize_drops_unknown_and_fills_defaults() -> None:
    out = sanitize_categories(
        {"deadline": {"email": False, "bogus": True}, "ghost": {"email": True}}
    )
    assert "ghost" not in out
    assert out["deadline"] == {"in_app": True, "email": False}
    assert "bogus" not in out["deadline"]
    # all canonical categories present
    assert set(out) == set(DEFAULT_CATEGORIES)
