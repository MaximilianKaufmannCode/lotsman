# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Fidelity tests for the xlsx import-session codec (pickle -> msgpack migration).

Guarantees that values parsed from an uploaded xlsx survive the Redis round-trip
byte-for-byte, so confirm writes the same data preview parsed — and that legacy
pickle sessions still load during the rollout window (dual-read).
"""

from __future__ import annotations

import datetime as dt
import gzip
import pickle
from decimal import Decimal

from registry_service.application.import_session_codec import dumps_session, loads_session


def test_round_trip_preserves_all_cell_types() -> None:
    data = {
        "headers": ["a", "b", "Имя", "数量"],
        "rows": [
            ["text", 42, 3.14, None, True, False],
            [Decimal("1234567890.5500"), dt.datetime(2026, 6, 4, 17, 30, 5)],
            [dt.date(2026, 1, 2), dt.time(9, 15, 30), "ИНН-7707083893"],
        ],
        "known_columns": [("a", "name"), ("b", "inn")],
        "unknown_headers": ["数量"],
        "nested": {"k": [Decimal("0.1"), {"x": None}]},
    }
    restored = loads_session(dumps_session(data))

    assert restored["headers"] == data["headers"]
    assert restored["rows"][0] == ["text", 42, 3.14, None, True, False]
    assert restored["rows"][1][0] == Decimal("1234567890.5500")
    assert restored["rows"][1][1] == dt.datetime(2026, 6, 4, 17, 30, 5)
    assert restored["rows"][2][0] == dt.date(2026, 1, 2)
    assert restored["rows"][2][1] == dt.time(9, 15, 30)
    assert restored["rows"][2][2] == "ИНН-7707083893"
    # tuples decode as lists (msgpack has no tuple type); callers unpack positionally
    assert restored["known_columns"] == [["a", "name"], ["b", "inn"]]
    assert restored["unknown_headers"] == ["数量"]
    assert restored["nested"]["k"][0] == Decimal("0.1")


def test_decimal_precision_is_exact() -> None:
    data = {"v": Decimal("0.10000000000000000000001")}
    assert loads_session(dumps_session(data))["v"] == Decimal("0.10000000000000000000001")


def test_dual_read_legacy_pickle_session() -> None:
    """Sessions written by the previous pickle-based version must still load."""
    legacy = {
        "headers": ["x"],
        "rows": [[1, "y"]],
        "known_columns": [],
        "unknown_headers": [],
    }
    raw = gzip.compress(pickle.dumps(legacy))
    assert loads_session(raw) == legacy
