# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Minimal markdown → HTML renderer + branded email wrapper.

No external dependencies — `markdown` library isn't installed and adding deps
requires container image rebuild on on-prem. Our templates use only:
  - **bold** → <strong>
  - paragraphs (separated by \\n\\n) → <p>
  - bare URLs → <a href>
  - one CTA-like line «Открыть документ в системе:\\n<url>» → styled button

Produces inline-CSS HTML for max email-client compatibility (Outlook, Gmail,
mobile). 600px max-width centered card.

NOT for arbitrary user-generated markdown — sanitization is by the closed
set of templates we ship + plain text fallback delivered alongside.
"""

from __future__ import annotations

import html
import re
from typing import Final

# ── markdown subset ────────────────────────────────────────────────────────────

_URL_RE: Final = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_BOLD_RE: Final = re.compile(r"\*\*([^*\n]+?)\*\*")
# Trailing CTA label that duplicates the button caption ("Открыть документ").
# Templates phrase the deep-link line as «…Открыть документ[ в системе]:\n<url>»;
# the button below already says «Открыть документ», so this prefix is redundant.
_CTA_LABEL_RE: Final = re.compile(
    r"\s*Открыть документ(?:\s+в\s+систем\w*)?\s*:?\s*$",
    re.IGNORECASE,
)


def _escape(text: str) -> str:
    """HTML-escape, but preserve already-escaped quotes."""
    return html.escape(text, quote=False)


def _render_paragraph(text: str) -> str:
    """Render single paragraph (text without \\n\\n). Inline conversions:
    - **bold** → <strong>bold</strong>
    - bare URL → <a href="URL">URL</a>
    - newlines → <br>
    """
    text = _escape(text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _URL_RE.sub(
        lambda m: (
            f'<a href="{m.group(0)}" '
            'style="color:#1d4ed8;text-decoration:underline;">'
            f"{m.group(0)}</a>"
        ),
        text,
    )
    text = text.replace("\n", "<br>")
    return text


def render_markdown_subset(body_md: str) -> str:
    """Render the markdown subset used in Лоцман templates → HTML fragment.

    Returns body HTML without surrounding <html>/<body> — use `wrap_branded`
    to compose the full email document.
    """
    paragraphs = [p.strip() for p in body_md.strip().split("\n\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        # Special-case the deep-link CTA: a paragraph that contains "{{document_url}}"
        # placeholder OR a bare URL — render as a styled button.
        # By this stage in send_document_reminder the placeholder has been
        # substituted, so we look for an https://… that looks like a deep-link
        # to the SPA (heuristic: contains '/registry').
        # Domain-agnostic: match any SPA deep-link by its '/registry' path, so the
        # button renders regardless of the configured WEB_BFF_URL host.
        m = re.search(
            r"https?://[^\s<>]*\bregistry\b[^\s<>]*",
            p,
            flags=re.IGNORECASE,
        )
        if m and (
            p.strip().endswith(m.group(0))
            or "\n" + m.group(0) in p
            or "\nhttp" in p
        ):
            # Split paragraph: prefix text + the URL → styled button below.
            before = p[: m.start()].rstrip(": \n")
            # Strip a trailing «Открыть документ[ в системе]:» label so it doesn't
            # duplicate the button caption rendered just below.
            before = _CTA_LABEL_RE.sub("", before).rstrip(": \n")
            url = m.group(0)
            if before:
                html_parts.append(
                    f'<p style="margin:0 0 16px 0;line-height:1.5;color:#1f2937;">'
                    f"{_render_paragraph(before)}</p>"
                )
            html_parts.append(
                '<p style="margin:0 0 24px 0;">'
                f'<a href="{url}" '
                'style="display:inline-block;padding:12px 24px;'
                "background:#1d4ed8;color:#ffffff;text-decoration:none;"
                'border-radius:6px;font-weight:600;">Открыть документ</a></p>'
            )
            continue

        html_parts.append(
            '<p style="margin:0 0 16px 0;line-height:1.5;color:#1f2937;">'
            f"{_render_paragraph(p)}</p>"
        )
    return "\n".join(html_parts)


# ── Branded wrapper ────────────────────────────────────────────────────────────


def wrap_branded(*, subject: str, body_html: str, footer_note: str | None = None) -> str:
    """Wrap body_html with a Лоцман-branded outer HTML — header + footer.

    600px centered card, inline CSS (Outlook 2016+ Gmail-safe).
    """
    pre_header = _escape(subject)
    footer = footer_note or (
        "Это автоматическое сообщение системы «Лоцман». "
        "Если вы получили его по ошибке — проигнорируйте письмо."
    )
    footer = _escape(footer)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;">
<!-- preheader hidden -->
<span style="display:none;font-size:0;line-height:0;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{pre_header}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
  <tr>
    <td align="center" style="padding:24px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <tr>
          <td style="padding:24px 32px;border-bottom:1px solid #e5e7eb;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="font-size:20px;font-weight:700;color:#0f172a;">⚓ Лоцман</td>
                <td align="right" style="font-size:12px;color:#6b7280;">Реестр документов</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            {body_html}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;border-top:1px solid #e5e7eb;background:#f9fafb;border-radius:0 0 8px 8px;">
            <p style="margin:0;font-size:12px;color:#6b7280;line-height:1.5;">{footer}</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""
