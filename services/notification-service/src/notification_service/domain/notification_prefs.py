# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Per-user notification preferences — domain defaults & resolution (ADR-0011 §D2).

Pure, dependency-free logic so it is trivially unit-testable (the project conventions —
domain coverage ≥90% branches). The DB row may be absent (user never saved
settings) or partially populated (new categories added after the row was
written); resolution always merges over the canonical defaults so callers get a
complete, valid view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# Canonical category ids — keep in sync with the requirements §2
# and the SQL default in alembic 0006.
DEFAULT_CATEGORIES: dict[str, dict[str, bool]] = {
    "doc_created": {"in_app": True, "email": False},
    "doc_updated": {"in_app": True, "email": False},
    "doc_assigned": {"in_app": True, "email": True},
    "doc_attachment": {"in_app": True, "email": False},
    "doc_archived": {"in_app": True, "email": False},
    "deadline": {"in_app": True, "email": True},
    "asset": {"in_app": False, "email": False},
}

CATEGORY_IDS: tuple[str, ...] = tuple(DEFAULT_CATEGORIES.keys())
VALID_CHANNELS: frozenset[str] = frozenset({"in_app", "email"})
VALID_EMAIL_MODES: frozenset[str] = frozenset({"instant", "digest", "off"})

DEFAULT_ENABLED = True
DEFAULT_SUPPRESS_OWN = True
DEFAULT_EMAIL_MODE = "digest"


class PrefsRow(Protocol):
    """Structural type for the ORM row (or any object) we resolve from."""

    enabled: bool
    suppress_own: bool
    email_mode: str
    categories: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EffectivePrefs:
    enabled: bool
    suppress_own: bool
    email_mode: str
    categories: dict[str, dict[str, bool]]


def effective(row: PrefsRow | None) -> EffectivePrefs:
    """Resolve a (possibly None / partial) prefs row to a complete view."""
    if row is None:
        return EffectivePrefs(
            enabled=DEFAULT_ENABLED,
            suppress_own=DEFAULT_SUPPRESS_OWN,
            email_mode=DEFAULT_EMAIL_MODE,
            categories={k: dict(v) for k, v in DEFAULT_CATEGORIES.items()},
        )

    stored = row.categories or {}
    cats: dict[str, dict[str, bool]] = {}
    for cid, default in DEFAULT_CATEGORIES.items():
        s = stored.get(cid) if isinstance(stored, dict) else None
        s = s or {}
        cats[cid] = {
            "in_app": bool(s.get("in_app", default["in_app"])),
            "email": bool(s.get("email", default["email"])),
        }

    mode = row.email_mode if row.email_mode in VALID_EMAIL_MODES else DEFAULT_EMAIL_MODE
    return EffectivePrefs(
        enabled=bool(row.enabled),
        suppress_own=bool(row.suppress_own),
        email_mode=mode,
        categories=cats,
    )


def wants(row: PrefsRow | None, category: str, channel: str) -> bool:
    """Does this user want `category` notifications on `channel`?

    Honors the master switch. Unknown category/channel → False (fail-closed).
    """
    eff = effective(row)
    if not eff.enabled:
        return False
    cat = eff.categories.get(category)
    if cat is None:
        return False
    return bool(cat.get(channel, False))


def sanitize_categories(raw: dict[str, Any] | None) -> dict[str, dict[str, bool]]:
    """Coerce client-supplied categories into the canonical shape.

    Drops unknown categories/channels; missing entries fall back to defaults.
    Used when persisting a PUT from the profile UI.
    """
    raw = raw or {}
    out: dict[str, dict[str, bool]] = {}
    for cid, default in DEFAULT_CATEGORIES.items():
        s = raw.get(cid) if isinstance(raw, dict) else None
        s = s if isinstance(s, dict) else {}
        out[cid] = {
            "in_app": bool(s.get("in_app", default["in_app"])),
            "email": bool(s.get("email", default["email"])),
        }
    return out
