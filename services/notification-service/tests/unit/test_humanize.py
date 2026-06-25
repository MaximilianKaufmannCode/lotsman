# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for Russian humanisation helpers (notification copy)."""

from __future__ import annotations

import pytest

from notification_service.infrastructure.humanize import (
    days_phrase,
    days_word,
    format_date_ru,
    plural_ru,
)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "день"),
        (2, "дня"),
        (3, "дня"),
        (4, "дня"),
        (5, "дней"),
        (11, "дней"),
        (12, "дней"),
        (14, "дней"),
        (21, "день"),
        (22, "дня"),
        (25, "дней"),
        (0, "дней"),
        (101, "день"),
    ],
)
def test_days_word(n: int, expected: str) -> None:
    assert days_word(n) == expected


def test_days_phrase() -> None:
    assert days_phrase(1) == "1 день"
    assert days_phrase(3) == "3 дня"
    assert days_phrase(5) == "5 дней"


def test_plural_ru_generic() -> None:
    assert plural_ru(1, "файл", "файла", "файлов") == "файл"
    assert plural_ru(3, "файл", "файла", "файлов") == "файла"
    assert plural_ru(5, "файл", "файла", "файлов") == "файлов"


def test_format_date_ru() -> None:
    # 2026-07-15 is a Wednesday → «ср»
    assert format_date_ru("2026-07-15") == "15 июля 2026, ср"
    # Accepts a full datetime ISO string (takes the date part).
    assert format_date_ru("2026-07-15T10:30:00Z") == "15 июля 2026, ср"


def test_format_date_ru_invalid_is_passthrough() -> None:
    assert format_date_ru("") == ""
    assert format_date_ru("not-a-date") == "not-a-date"
