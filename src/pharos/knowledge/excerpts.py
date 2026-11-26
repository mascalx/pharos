"""Source-faithful previews for RAG diagnostics; never replace full evidence."""
from __future__ import annotations

import re


def relevant_excerpt(text: str, question: str, max_chars: int = 230) -> str:
    """Return a brief window with the greatest lexical question overlap.

    A multilingual, deterministic display heuristic, not retrieval or rerank.
    Original text and its page/chunk identifiers remain unchanged.
    """
    if max_chars < 60:
        raise ValueError('max_chars must be at least 60')
    clean = ' '.join(str(text).split())
    if len(clean) <= max_chars:
        return clean
    lowered = clean.casefold()
    query_words = re.findall(r'[^\W_]+', question.casefold())
    stems = list(dict.fromkeys(word[:6] for word in query_words
                               if len(word) >= 4 or word.isdigit()))
    starts = {0}
    for stem in stems[:16]:
        for match in list(re.finditer(re.escape(stem), lowered))[:32]:
            starts.add(max(0, match.start() - max_chars // 4))
    def rank(start: int) -> tuple[int, int, int]:
        window = lowered[start:start + max_chars]
        return (sum(stem in window for stem in stems),
                sum(min(window.count(stem), 2) for stem in stems), -start)
    start = max(starts, key=rank)
    # A lone generic match should not hide the page heading or menu path.
    if len(stems) > 1 and rank(start)[0] < 2:
        start = 0
    if start > 0:
        boundary = clean.find(' ', start, min(start + 24, len(clean)))
        if boundary >= 0:
            start = boundary + 1
    # Reserve one character for the trailing ellipsis when the text continues.
    allowance = max_chars - (2 if start else 0) - 1
    end = min(len(clean), start + allowance)
    body = clean[start:end].rstrip()
    return ('… ' if start else '') + body + ('…' if end < len(clean) else '')
