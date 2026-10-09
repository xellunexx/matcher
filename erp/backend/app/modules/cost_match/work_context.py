"""Resolve subordinate dimensions from explicit work headings, never from trades."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.modules.cost_match.bulgarian import is_structural, parse_work


_DIMENSION_ONLY = re.compile(r'^\s*[фФØø∅]\s*\d+(?:[.,]\d+)?\s*(?:mm|мм)?\s*$', re.I)
_TRADE_HEADING = re.compile(r'^(?:част\b.*|водопровод|канализация|арматури|овк|електро|'
                            r'сградно водопроводно отклонение)$', re.I)


def is_work_fragment(description: str) -> bool:
    return bool(_DIMENSION_ONLY.fullmatch(description))


def effective_work(description: str, parents: Sequence[str] = ()) -> str:
    if not is_work_fragment(description):
        return description
    for parent in reversed(parents):
        text = parent.strip()
        if is_structural(text) or _TRADE_HEADING.fullmatch(text):
            break
        if parse_work(text).object:
            return f'{text}; {description}'
        break
    return description


def position_contexts(positions: Sequence[Any]) -> dict[Any, list[str]]:
    """Use a BOQ's explicit parent tree; flat imports use only section rows."""
    by_id = {position.id: position for position in positions}
    contexts = {}
    flat_parent = ''
    for position in positions:
        description = position.description or ''
        if (position.unit or '').strip().lower() in ('', 'section'):
            flat_parent = description
        parents: list[str] = []
        visited = {position.id}
        parent_id = getattr(position, 'parent_id', None)
        while parent_id in by_id and parent_id not in visited and len(parents) < 8:
            visited.add(parent_id)
            parent = by_id[parent_id]
            parents.append(parent.description or '')
            parent_id = getattr(parent, 'parent_id', None)
        if parent_id in visited:
            parents = []
        if parents:
            parents.reverse()
        elif getattr(position, 'parent_id', None) is None and flat_parent:
            parents = [flat_parent]
        contexts[position.id] = parents if is_work_fragment(description) else []
        if (position.unit or '').strip().lower() not in ('', 'section') and not is_work_fragment(description):
            flat_parent = ''
    return contexts
