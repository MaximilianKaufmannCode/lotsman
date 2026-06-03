# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""MIME type sniffer — libmagic primary, OOXML zip-peek fallback.

Two-layer detection so real-world Office documents (xlsx / docx / pptx) are
not misidentified as the generic `application/zip` or `application/octet-stream`:

1. libmagic (python-magic, libmagic1 C library) — primary detector. Works
   for pdf, jpeg, png, tiff and most other formats, including OOXML files
   whose `[Content_Types].xml` happens to be near the start of the archive.
2. ZIP-peek fallback — if libmagic returns a generic ZIP/octet-stream and
   the buffer parses as a ZIP archive, we open the central directory and
   read `[Content_Types].xml` to determine the OOXML subtype. The central
   directory is at the END of a ZIP, so libmagic's sequential signature
   scan can't reach it on real-world Office docs that pad the front with
   thumbnails, custom styles, embedded media, etc. — but Python's zipfile
   reads from the end, so we always find it if the file is well-formed.

The combination keeps the "trust bytes, not extension" property: extension
is never consulted; the ZIP central directory is data inside the file.
"""

from __future__ import annotations

import io
import zipfile

import structlog

log = structlog.get_logger(__name__)

try:
    import magic as _magic  # type: ignore[import-untyped]

    _MAGIC_AVAILABLE = True
except ImportError:
    _MAGIC_AVAILABLE = False
    log.warning(
        "libmagic_unavailable",
        detail=(
            "python-magic / libmagic not found. "
            "MIME sniffing falls back to mimetypes (extension-based, less secure). "
            "Install libmagic1 in the container image."
        ),
    )


# OOXML content-type overrides that we recognise. Keys are substrings of the
# corresponding `<Override ContentType="…">` declarations inside
# `[Content_Types].xml`; values are the canonical MIME types matching
# registry_service.application.policies.attachment_policy.ALLOWED_MIME.
_OOXML_CONTENT_TYPE_MAP: tuple[tuple[str, str], ...] = (
    (
        "officedocument.spreadsheetml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    (
        "officedocument.wordprocessingml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        "officedocument.presentationml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
)


def _detect_ooxml_subtype(data: bytes) -> str | None:
    """If *data* is a well-formed ZIP containing `[Content_Types].xml`,
    return the OOXML MIME subtype declared inside; otherwise None.

    Uses Python's zipfile which reads the central directory from the end of
    the archive — independent of where in the file `[Content_Types].xml`
    physically appears in local-file-header order.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if "[Content_Types].xml" not in names:
                return None
            try:
                ct_xml_bytes = zf.read("[Content_Types].xml")
            except (KeyError, zipfile.BadZipFile):
                return None
    except (zipfile.BadZipFile, OSError, ValueError):
        return None

    try:
        ct_xml = ct_xml_bytes.decode("utf-8", errors="replace")
    except UnicodeError:
        return None

    for marker, mime in _OOXML_CONTENT_TYPE_MAP:
        if marker in ct_xml:
            return mime
    return None


class LibmagicMimeSniffer:
    """MIME sniffer backed by libmagic (python-magic) with OOXML zip-peek fallback.

    Strategy:
      1. libmagic.from_buffer over a generous window (64 KB).
      2. If libmagic returns the generic `application/zip` /
         `application/octet-stream` AND the buffer is a well-formed ZIP that
         contains `[Content_Types].xml`, return the OOXML subtype declared
         inside.
      3. Otherwise return whatever libmagic gave us (or `octet-stream` on
         outright failure — deny-by-default vs the attachment_policy
         allowlist).
    """

    # 64 KB is enough for libmagic to see most OOXML headers; the ZIP-peek
    # fallback handles the remainder. Bounded to keep CPU/memory predictable.
    _SNIFF_LIMIT = 64 * 1024

    _GENERIC_MIMES = frozenset({"application/zip", "application/octet-stream"})

    def sniff(self, data: bytes) -> str:
        primary = "application/octet-stream"
        if _MAGIC_AVAILABLE:
            try:
                primary = str(_magic.from_buffer(data[: self._SNIFF_LIMIT], mime=True))
            except Exception as exc:
                log.warning("magic_sniff_failed", error=str(exc))

        if primary in self._GENERIC_MIMES:
            ooxml = _detect_ooxml_subtype(data)
            if ooxml is not None:
                return ooxml

        return primary


class FallbackMimeSniffer:
    """Extension-based MIME guesser for environments without libmagic (tests only)."""

    def sniff(self, data: bytes) -> str:
        # Cannot guess from bytes alone — callers should pass filename separately.
        # Returns generic type; tests should mock this.
        return "application/octet-stream"


def get_mime_sniffer() -> LibmagicMimeSniffer:
    """Factory for the production MIME sniffer."""
    return LibmagicMimeSniffer()
