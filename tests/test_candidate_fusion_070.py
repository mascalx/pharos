"""0.7.0.dev5 candidate-only fusion: no index mutation, old search is default."""
from __future__ import annotations

from pharos.knowledge.rag import _fuse_vector_lexical, _retrieval_tokens
from pharos.app import SearchPayload


def _hit(score, text, filename='doc.txt'):
    return {'score': score, 'text': text, 'filename': filename}


def test_unicode_tokens_and_technical_identifiers():
    assert {'qwen3', 'embedding', '4b'} <= _retrieval_tokens('QWEN3_embedding-4B')
    assert 'verifica' in _retrieval_tokens('Verifica dei parametri')
    assert '123' in _retrieval_tokens('123')


def test_lexical_evidence_promotes_lower_ranked_vector_candidate():
    examples = [
        _hit(0.99, 'unrelated subject', 'unrelated.txt'),
        _hit(0.98, 'different topic', 'different.txt'),
        _hit(0.97, 'unrelated facts', 'more.txt'),
        _hit(0.96, 'another topic', 'another.txt'),
        _hit(0.95, 'special diagnostics code HJX-987 detailed explanation', 'manual.txt'),
    ]
    ranked = _fuse_vector_lexical(examples, 'HJX-987 diagnostics')
    assert ranked[0]['filename'] == 'manual.txt'
    assert ranked[0]['score'] == 0.95
    assert len(ranked) == len(examples)


def test_zero_lexical_overlap_keeps_vector_order():
    examples = [_hit(0.7, 'apples'), _hit(0.9, 'oranges'), _hit(0.8, 'pears')]
    ranked = _fuse_vector_lexical(examples, 'xyz unknown')
    assert [x['score'] for x in ranked] == [0.9, 0.8, 0.7]


def test_empty_list_and_nonlatin_script():
    assert _fuse_vector_lexical([], 'query') == []
    assert _retrieval_tokens('地图 маршрут المعالم')


def test_search_api_defaults_to_legacy_vector_mode():
    payload = SearchPayload(kb_ids=['sample'], question='test', limit=5)
    assert payload.retrieval_mode == 'vector'
    assert SearchPayload(kb_ids=['sample'], question='test',
                         retrieval_mode='candidate_fusion').retrieval_mode == 'candidate_fusion'
