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


# ── Notification email (structured: status accent + details + CTA + dark mode) ──

# Accent colour per urgency status. Mirrors the SPA status tokens
# (status-ok / status-soon / status-overdue) so email and app agree visually.
STATUS_ACCENT: Final[dict[str, str]] = {
    "overdue": "#dc2626",  # red — просрочено
    "today": "#d97706",    # amber — истекает сегодня
    "soon": "#d97706",     # amber — скоро
    "ok": "#16a34a",       # green — актуально / создано
    "info": "#1d4ed8",     # blue — нейтральное событие (default)
}

# Dark-mode + responsive overrides. Inline styles below carry the light theme;
# clients that honour prefers-color-scheme (Apple Mail, others) apply these.
# Class hooks let the media query override inline styles via !important.
_EMAIL_STYLE: Final = """
  @media (prefers-color-scheme: dark) {
    .email-bg { background:#0b1220 !important; }
    .email-card { background:#111827 !important; box-shadow:none !important; }
    .email-hd, .email-ft { border-color:#1f2937 !important; }
    .email-ft { background:#0f172a !important; }
    .email-text { color:#e5e7eb !important; }
    .email-muted { color:#9ca3af !important; }
    .email-brand { color:#f1f5f9 !important; }
    .email-details { background:#0f172a !important; border-color:#1f2937 !important; }
  }
  @media only screen and (max-width:600px) {
    .email-pad { padding:24px 20px !important; }
  }
"""


def _render_details(details: list[tuple[str, str]]) -> str:
    """Render a key→value summary block. Rows with empty/'—' values are skipped."""
    rows = []
    for label, value in details:
        v = "" if value is None else str(value).strip()
        if v in ("", "—"):
            continue
        rows.append(
            '<tr>'
            '<td class="email-muted" style="padding:5px 14px 5px 0;font-size:13px;'
            'color:#6b7280;white-space:nowrap;vertical-align:top;">'
            f"{_escape(str(label))}</td>"
            '<td class="email-text" style="padding:5px 0;font-size:14px;color:#1f2937;'
            f'font-weight:600;">{_escape(v)}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 24px 0;">'
        '<tr><td class="email-details" style="background:#f9fafb;border:1px solid #e5e7eb;'
        'border-radius:8px;padding:10px 16px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f'{"".join(rows)}'
        "</table></td></tr></table>"
    )


def render_notification_email(
    *,
    subject: str,
    headline: str,
    intro_html: str = "",
    details: list[tuple[str, str]] | None = None,
    cta_url: str | None = None,
    cta_label: str = "Открыть документ",
    settings_url: str | None = None,
    status: str = "info",
    footer_note: str | None = None,
) -> str:
    """Compose a branded notification email with a status accent, an at-a-glance
    details block, and a single primary CTA. Inline-CSS + dark-mode aware.

    `headline` is shown in the status-coloured accent bar; `intro_html` is the
    rendered lead paragraph(s); `details` is a list of (label, value) rows.
    """
    accent = STATUS_ACCENT.get(status, STATUS_ACCENT["info"])
    pre_header = _escape(subject)
    footer = _escape(
        footer_note
        or (
            "Это автоматическое сообщение системы «Лоцман». "
            "Если вы получили его по ошибке — просто проигнорируйте письмо."
        )
    )

    accent_block = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 20px 0;">'
        f'<tr><td style="border-left:4px solid {accent};padding:4px 0 4px 14px;">'
        f'<span class="email-text" style="font-size:17px;font-weight:700;color:#0f172a;'
        f'line-height:1.4;">{_escape(headline)}</span>'
        "</td></tr></table>"
    )
    details_block = _render_details(details) if details else ""
    cta_block = ""
    if cta_url:
        cta_block = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 8px 0;">'
            f'<tr><td style="border-radius:6px;background:{accent};">'
            f'<a href="{_escape(cta_url)}" style="display:inline-block;padding:12px 28px;'
            'color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;">'
            f"{_escape(cta_label)} →</a></td></tr></table>"
        )
    settings_block = ""
    if settings_url:
        settings_block = (
            '<p style="margin:8px 0 0 0;font-size:12px;" class="email-muted">'
            f'<a href="{_escape(settings_url)}" style="color:#6b7280;text-decoration:underline;">'
            "Настроить уведомления</a></p>"
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_escape(subject)}</title>
<style>{_EMAIL_STYLE}</style>
</head>
<body class="email-bg" style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;">
<span style="display:none;font-size:0;line-height:0;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{pre_header}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-bg" style="background:#f3f4f6;">
  <tr>
    <td align="center" style="padding:24px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" class="email-card" style="max-width:600px;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <tr>
          <td class="email-hd" style="padding:20px 32px;border-bottom:1px solid #e5e7eb;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td class="email-brand" style="font-size:20px;font-weight:700;color:#0f172a;">⚓ Лоцман</td>
                <td align="right" class="email-muted" style="font-size:12px;color:#6b7280;">Реестр документов</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td class="email-pad" style="padding:32px;">
            {accent_block}
            {intro_html}
            {details_block}
            {cta_block}
          </td>
        </tr>
        <tr>
          <td class="email-ft" style="padding:16px 32px;border-top:1px solid #e5e7eb;background:#f9fafb;border-radius:0 0 8px 8px;">
            <p style="margin:0;font-size:12px;color:#6b7280;line-height:1.5;" class="email-muted">{footer}</p>
            {settings_block}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""


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
