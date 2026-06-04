# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Safe (de)serialization for xlsx import sessions — replaces pickle (CWE-502).

Import-preview sessions are stored in Redis as **gzipped msgpack**. The previous
implementation used ``pickle``; because the stored payload includes values parsed
from a user-uploaded xlsx, ``pickle.loads`` on that data is an arbitrary-code-execution
risk if Redis is ever compromised or a key is attacker-influenced.

A legacy pickle read path is retained for ONE release (``loads_session`` falls back
to it) so that sessions written by the previous version still load during rollout.
Remove ``loads_session_legacy_pickle`` and its fallback once all such sessions have
expired (TTL-bounded).

Type fidelity: datetime is preserved via msgpack's native timestamp extension;
Decimal / date / time are preserved via dedicated ext types so that values written
to the registry on confirm are byte-for-byte equivalent to the parsed input.
Note: tuples are decoded as lists (msgpack has no tuple type) — callers unpack
``known_columns`` positionally, so this is behaviour-preserving.
"""

from __future__ import annotations

import datetime as _dt
import gzip
from decimal import Decimal
from typing import Any

import msgpack

_EXT_DECIMAL = 1
_EXT_DATE = 2
_EXT_TIME = 3
_EXT_DATETIME = 4


def _default(obj: Any) -> msgpack.ExtType:
    if isinstance(obj, Decimal):
        return msgpack.ExtType(_EXT_DECIMAL, str(obj).encode("utf-8"))
    # datetime MUST be checked before date (datetime is a subclass of date).
    # We encode datetime ourselves (isoformat) rather than rely on msgpack's
    # datetime=True, which does not handle naive datetimes — and openpyxl yields
    # naive datetimes. isoformat round-trips naive and tz-aware values exactly.
    if isinstance(obj, _dt.datetime):
        return msgpack.ExtType(_EXT_DATETIME, obj.isoformat().encode("utf-8"))
    if isinstance(obj, _dt.date):
        return msgpack.ExtType(_EXT_DATE, obj.isoformat().encode("utf-8"))
    if isinstance(obj, _dt.time):
        return msgpack.ExtType(_EXT_TIME, obj.isoformat().encode("utf-8"))
    raise TypeError(f"Cannot serialize import-session value of type {type(obj)!r}")


def _ext_hook(code: int, data: bytes) -> Any:
    if code == _EXT_DECIMAL:
        return Decimal(data.decode("utf-8"))
    if code == _EXT_DATE:
        return _dt.date.fromisoformat(data.decode("utf-8"))
    if code == _EXT_TIME:
        return _dt.time.fromisoformat(data.decode("utf-8"))
    if code == _EXT_DATETIME:
        return _dt.datetime.fromisoformat(data.decode("utf-8"))
    return msgpack.ExtType(code, data)


def dumps_session(data: dict[str, Any]) -> bytes:
    """Serialize a session dict to gzipped msgpack bytes."""
    packed = msgpack.packb(data, default=_default, use_bin_type=True)
    return gzip.compress(packed, compresslevel=6)


def loads_session(raw: bytes) -> dict[str, Any]:
    """Deserialize gzipped msgpack bytes; fall back to legacy pickle for one release."""
    try:
        decompressed = gzip.decompress(raw)
        return msgpack.unpackb(
            decompressed,
            ext_hook=_ext_hook,
            raw=False,
            strict_map_key=False,
        )
    except Exception:
        # Dual-read fallback: sessions written by the pre-msgpack version.
        return loads_session_legacy_pickle(raw)


def loads_session_legacy_pickle(raw: bytes) -> dict[str, Any]:
    """DEPRECATED dual-read for sessions written by the pre-msgpack version.

    Remove in the next release once all in-flight pickle sessions have expired.
    Reads only internal, server-written Redis data during the rollout window.
    """
    import pickle  # noqa: S403 — legacy fallback only, internal Redis payload

    result: dict[str, Any] = pickle.loads(gzip.decompress(raw))  # noqa: S301
    return result
