"""Validate generated numeric RAG references against the passages supplied to an LLM.

This module never invents or resolves a source. It only normalizes references
already written by the model, removes out-of-range labels and reports defects.
"""
from __future__ import annotations

import re

# Match compact or grouped citation labels only; avoid arbitrary Markdown text.
_GROUP = re.compile(r'\[([ \t]*\d[0-9,; \t\-–—]{0,75})\]')
_RANGE = re.compile(r'([0-9]{1,3})[ \t]*[-–—][ \t]*([0-9]{1,3})')
_SINGLE = re.compile(r'[0-9]{1,3}')


def normalize_answer_citations(answer: str, source_count: int) -> tuple[str, set[int], bool]:
    """Return normalized answer, cited 1-based indices and missing/invalid flag.

    Supports [1], [1, 7], [1; 7] and [2-4]. Each numeric reference must
    identify a supplied passage. Invalid labels are removed and flagged; their
    source IDs are never fabricated. Unrelated nonnumeric brackets are kept.
    """
    if source_count < 0:
        raise ValueError('source_count must not be negative')
    cited: set[int] = set()
    invalid = False

    def rewrite(match: re.Match[str]) -> str:
        nonlocal invalid
        body = match.group(1).strip()
        references: list[int] = []
        parts = re.split(r'[;,]', body)
        if any(not part.strip() for part in parts):
            invalid = True
            return ''
        for raw in parts:
            part = raw.strip()
            range_match = _RANGE.fullmatch(part)
            if range_match:
                first, last = map(int, range_match.groups())
                if first > last or last - first >= 24:
                    invalid = True
                    continue
                references.extend(range(first, last + 1))
            elif _SINGLE.fullmatch(part):
                references.append(int(part))
            else:
                invalid = True
        valid: list[int] = []
        for ref in references:
            if 1 <= ref <= source_count:
                if ref not in valid:
                    valid.append(ref)
                cited.add(ref)
            else:
                invalid = True
        return ' '.join(f'[{ref}]' for ref in valid)

    normalized = _GROUP.sub(rewrite, answer)
    # A numeric-looking reference outside supported grammar is not evidence.
    # Leave the text untouched, but warn instead of treating it as sourced.
    for bracket in re.finditer(r'\[([^\[\]\n]{1,80})\]', normalized):
        label = bracket.group(1).strip()
        if label and label[0].isdigit() and not _SINGLE.fullmatch(label):
            invalid = True
    return normalized, cited, invalid or not bool(cited)
