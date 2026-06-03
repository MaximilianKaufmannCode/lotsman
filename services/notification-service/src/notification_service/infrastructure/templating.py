# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Минимальный {{placeholder}} renderer для email/telegram/dion-шаблонов.

Шаблоны хранятся в notification.message_templates (body_md), используют
mustache-style плейсхолдеры `{{variable_name}}`. Никаких условных конструкций
или циклов — для текущих 3 use-cases (pre_notice / in_day / overdue) этого
достаточно.

Если в будущем понадобятся `{% if %}` или `{% for %}` — заменить на Jinja2
(один helper-функция → переписать; контракт `render_template(template, vars)
→ str` останется).

Принципиальное решение: не добавлять Jinja2 как новую зависимость, чтобы
не требовать rebuild контейнерных images на on-prem проде. Custom renderer
≈ 20 строк, поведение предсказуемое, легко тестируется.
"""

from __future__ import annotations

import re
from typing import Any


class TemplateRenderError(Exception):
    """Raised when a template references a variable not provided in `vars`."""


# Match `{{ var_name }}` with optional whitespace; capture the variable name.
# Variable names must match Python-identifier convention (snake_case alphanumeric+_).
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_template(template: str, variables: dict[str, Any]) -> str:
    """Substitute `{{name}}` placeholders with values from `variables`.

    Args:
        template: Template string with `{{name}}` placeholders.
        variables: Dict of name → value. Values are converted via str().

    Returns:
        Rendered string.

    Raises:
        TemplateRenderError: If a placeholder references a name not in `variables`.

    Example:
        >>> render_template("Hello, {{full_name}}!", {"full_name": "Иван"})
        'Hello, Иван!'
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise TemplateRenderError(
                f"Template variable not provided: {key!r}. "
                f"Available: {sorted(variables.keys())}"
            )
        return str(variables[key])

    return _PLACEHOLDER_RE.sub(_replace, template)


def extract_placeholders(template: str) -> set[str]:
    """Return the set of placeholder names referenced in the template.

    Useful for template-editor UI to show required variables.

    Example:
        >>> sorted(extract_placeholders("{{a}} {{b}} {{ a }}"))
        ['a', 'b']
    """
    return {m.group(1) for m in _PLACEHOLDER_RE.finditer(template)}
