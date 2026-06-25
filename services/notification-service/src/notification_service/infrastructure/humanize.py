# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Russian-language humanisation helpers for notification copy.

Pure functions, no I/O — used to turn raw ISO dates / day counts into
reader-friendly Russian ("15 июля 2026, ср", "через 3 дня") so notification
emails communicate *when* and *how soon* at a glance instead of "2026-07-15
(через 3 дн.)".
"""

from __future__ import annotations

from datetime import date

_MONTHS_GEN = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Pick the Russian plural form for `n` (e.g. день / дня / дней)."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def days_word(n: int) -> str:
    """'день' / 'дня' / 'дней' for `n`."""
    return plural_ru(n, "день", "дня", "дней")


def days_phrase(n: int) -> str:
    """'3 дня' / '1 день' / '5 дней'."""
    return f"{n} {days_word(n)}"


def format_date_ru(iso: str) -> str:
    """ISO date (or datetime) → 'D месяца YYYY, дн' (e.g. '15 июля 2026, ср').

    Returns the input unchanged if it cannot be parsed, so a malformed value
    never breaks a notification.
    """
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        return iso
    return f"{d.day} {_MONTHS_GEN[d.month]} {d.year}, {_WEEKDAYS_SHORT[d.weekday()]}"
