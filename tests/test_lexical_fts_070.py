"""Regression tests for optional, rebuildable PHAROS full-corpus lexical indexes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from pharos.knowledge.lexical import (LexicalIndex, _query_terms, _distinctive_terms,
                                       _lexical_strength, fuse_full_corpus)
from pharos.knowledge_contract import RAGError


@pytest.fixture(autouse=True)
def require_sqlite_fts5():
    with sqlite3.connect(':memory:') as db:
        try:
            db.execute('CREATE VIRTUAL TABLE __fts_probe USING fts5(text)')
        except sqlite3.OperationalError:
            pytest.skip('FTS5 not enabled in this Python SQLite build')


class FakeEngine:
    def __init__(self, root: Path):
        self.config = SimpleNamespace(storage=SimpleNamespace(database_dir=root))
        self.rag_db = root / 'rag.sqlite3'
        self.kb_id = str(uuid4())
        self.doc_id = str(uuid4())
        self.reindex_in_progress = False
        self.collection = 'pharos_kb_fake'
        self.fail_scan = False
        self.points = [
            {'payload': {
                'kb_id': self.kb_id, 'document_id': self.doc_id,
                'filename': 'Manual.pdf', 'page': 9,
                'chunk_index': 0, 'text': 'Procedura di configurazione wireless.'}},
            {'payload': {
                'kb_id': self.kb_id, 'document_id': self.doc_id,
                'filename': 'Manual.pdf', 'page': 128,
                'chunk_index': 1, 'text': 'Codice HJX-987: reset di fabbrica.'}}
        ]
        with self._db() as db:
            db.execute('CREATE TABLE documents (id TEXT, kb_id TEXT, filename TEXT,'
                       'sha256 TEXT,status TEXT,chunks INTEGER)')
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',
                       (self.doc_id, self.kb_id, 'Manual.pdf', 'hash', 'indexed', 2))

    def _db(self):
        connection = sqlite3.connect(self.rag_db)
        connection.row_factory = sqlite3.Row
        return connection

    def _uuid(self, value):
        return str(UUID(value))

    def get_kb(self, kb_id):
        assert kb_id == self.kb_id
        return {'id': kb_id, 'embedding_model': 'qwen3-embedding',
                'dimensions': 1024, 'name': 'test'}

    def _collection(self, kb_id):
        return self.collection

    def _enabled(self):
        pass

    def _check_volume(self):
        pass

    async def _qdrant(self, method, endpoint, payload):
        if self.fail_scan:
            raise RAGError('vector_store_unavailable')
        assert method == 'POST' and '/points/scroll' in endpoint
        offset = payload.get('offset', 0)
        start = int(offset)
        page = self.points[start:start + 1]
        next_offset = start + 1 if start + 1 < len(self.points) else None
        return {'result': {'points': page, 'next_page_offset': next_offset}}


def test_full_corpus_build_search_and_snapshot_freshness(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    assert index.status(engine.kb_id)['state'] == 'missing'
    events = []
    assert asyncio.run(index.build(engine.kb_id, uuid4().hex,
                                   lambda done,total: events.append((done,total))))['chunks'] == 2
    assert events[-1] == (2,2)
    assert index.status(engine.kb_id)['state'] == 'ready'
    matches = index.search(engine.kb_id, 'HJX-987', 5)
    assert len(matches) >= 1
    assert matches[0]['page'] == 128 and matches[0]['chunk_index'] == 1
    assert matches[0]['score'] is None
    assert matches[0]['document_id'] == engine.doc_id
    assert 'Manual.pdf' == matches[0]['filename']

    # Rename/insertion/deletion/reindex all invalidate the derived generation.
    with engine._db() as db:
        db.execute('UPDATE documents SET filename=?', ('New Name.pdf',))
    assert index.status(engine.kb_id)['state'] == 'stale'
    assert index.search(engine.kb_id, 'HJX-987', 5) == []


def test_failed_rebuild_keeps_previous_generation(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    old_matches = index.search(engine.kb_id, 'HJX', 5)
    engine.fail_scan = True
    with pytest.raises(RAGError, match='vector_store_unavailable'):
        asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    assert index.status(engine.kb_id)['state'] == 'ready'
    assert index.search(engine.kb_id, 'HJX', 5) == old_matches


def test_source_modified_during_index_build_does_not_publish(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)

    def mutate_catalog(done, total):
        if done == 1:
            with engine._db() as db:
                db.execute("UPDATE documents SET sha256='new-content'")

    with pytest.raises(RAGError, match='lexical_source_changed'):
        asyncio.run(index.build(engine.kb_id, uuid4().hex, mutate_catalog))
    assert index.status(engine.kb_id)['state'] == 'missing'


def test_missing_chunk_refuses_publication(tmp_path):
    engine = FakeEngine(tmp_path)
    engine.points = engine.points[:1]
    index = LexicalIndex(engine)
    with pytest.raises(RAGError, match='lexical_incomplete_index'):
        asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    assert index.status(engine.kb_id)['state'] == 'missing'


def test_query_escaping_and_vector_fallback():
    expression = _query_terms('"; DROP TABLE chunks; --')
    assert 'DROP TABLE' not in expression
    assert '"drop"' in expression
    assert _query_terms('') == ''
    vector = [{'kb_id': 'kb', 'document_id': 'one', 'chunk_index': 1,
               'score': 0.8, 'text': 'unrelated'}]
    lexical = [{'kb_id': 'kb', 'document_id': 'two', 'chunk_index': 5,
                'score': None, 'text': 'technical identifier HJX-987'}]
    both = fuse_full_corpus(vector, lexical)
    assert {r['document_id'] for r in both} == {'one','two'}
    assert next(r for r in both if r['document_id']=='two')['score'] is None
    assert fuse_full_corpus(vector, [])[0]['score'] == 0.8


def test_disallow_symlinked_index_directory(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (tmp_path / 'rag-lexical').symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip('Symlinks not available on this operating system')
    with pytest.raises(RAGError, match='lexical_unsafe_directory'):
        index.status(engine.kb_id)



def test_purge_removes_only_derived_fts_files(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    folder = index._folder(engine.kb_id)
    assert any(folder.iterdir())
    index.purge(engine.kb_id)
    assert not folder.exists()
    assert index.status(engine.kb_id)['state'] == 'missing'
    with engine._db() as db:
        assert db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 1


def test_purge_fails_closed_on_unknown_files(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    folder = index._safe_directory(engine.kb_id, create=True)
    unrelated = folder / 'user-notes.txt'
    unrelated.write_text('keep this')
    with pytest.raises(RAGError, match='lexical_cleanup_unsafe'):
        index.purge(engine.kb_id)
    assert unrelated.read_text() == 'keep this'


def test_dev10_selects_corpus_rare_terms_without_language_stopword_tables():
    with sqlite3.connect(':memory:') as db:
        db.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, tokenize='unicode61')")
        db.executemany('INSERT INTO chunks(text) VALUES (?)',
                       [('come si configura un documento',)] * 90 +
                       [('codice HJX-987 descritto nel manuale',)] +
                       [('connessione wireless',)] * 9)
        selected = _distinctive_terms(db, 'come si configura il codice HJX-987')
        assert 'come' not in selected and 'configura' not in selected
        assert {'codice','hjx','987'} <= set(selected)


def test_dev10_generic_single_token_overlap_is_not_lexical_evidence():
    assert _lexical_strength('Wi-Fi Protected Setup per audio',
                             'Come si configura il Wi-Fi?', ['configura']) == 0
    assert _lexical_strength('Il televisore si configura automaticamente',
                             'Come si configura il Wi-Fi?', ['configura']) == 0


def test_dev10_exact_identifier_promotes_strong_lexical_only_evidence():
    code_question = 'Qual è la procedura per HJX-987?'
    text = 'Codice tecnico HJX-987 e procedura di ripristino.'
    strength = _lexical_strength(text, code_question, ['hjx','987'])
    assert strength == 2.4
    vectors = [{'kb_id':'kb', 'document_id':'other', 'chunk_index':0,
                'score':0.91,'text':'other'}]
    lexical = [{'kb_id':'kb','document_id':'manual','chunk_index':23,
                'score':None,'text':text,'_lexical_weight':strength}]
    output = fuse_full_corpus(vectors, lexical)
    assert output[0]['document_id'] == 'manual'
    assert output[0]['score'] is None
    assert '_lexical_weight' not in output[0]
    assert output[1]['score'] == 0.91


def test_dev10_weak_fts_returns_unchanged_vector_results():
    vector = [{'kb_id':'kb','document_id':'manual','chunk_index':0,
               'score':0.9,'text':'correct semantic match'}]
    lexical = [{'kb_id':'kb','document_id':'another','chunk_index':1,
                'score':None,'text':'unrelated','_lexical_weight':0}]
    assert fuse_full_corpus(vector, lexical) == vector
    assert vector[0].get('retrieval') is None


def test_dev10_fused_ranks_handle_legacy_chunk_ids_and_ties():
    vectors = [{'kb_id':'kb','document_id':'x','chunk_index':0,
                'score':0.8,'text':'a'},
               {'kb_id':'kb','document_id':'x','chunk_index':None,
                'filename':'doc','page':1,'score':0.7,'text':'b'}]
    lexical = [{'kb_id':'kb','document_id':'y','chunk_index':1,
                'score':None,'text':'technical','_lexical_weight':0.75}]
    fused = fuse_full_corpus(vectors, lexical)
    assert len(fused) == 3
    assert any(row['score'] == 0.7 for row in fused)


def test_dev10_read_existing_fts5_generation_without_rebuild(tmp_path):
    engine = FakeEngine(tmp_path)
    index = LexicalIndex(engine)
    asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    generation_before = index._active_file(engine.kb_id)
    assert index.search(engine.kb_id, 'Come si configura la rete wireless?', 5) == []
    hits = index.search(engine.kb_id, 'HJX-987', 5)
    assert len(hits) == 1 and hits[0]['_lexical_weight'] == 2.4
    assert index._active_file(engine.kb_id) == generation_before


def test_dev10_unicode_accent_variants_preserve_lexical_evidence():
    # unicode61 removes Latin diacritics by default. The evidence check must
    # not disagree with an FTS token that successfully matched.
    from pharos.knowledge.lexical import _fold_term
    assert _fold_term('caffè') == 'caffe'
    assert _lexical_strength('Manuale: Café réglage', 'cafe reglage',
                             ['cafe', 'reglage']) == 0.012


def test_dev11_weak_software_matches_do_not_promote_troubleshooting():
    """Scattered software-update words cannot displace strong vector results."""
    question = 'Come si aggiorna il software?'
    text = ('Se persiste il problema, aggiornare il software in seguito. '
            'Consultare prima la diagnostica di rete e il server DNS.')
    weight = _lexical_strength(text, question, ['aggiornare', 'software'])
    assert 0 < weight <= 0.035
    vectors = [
        {'kb_id':'kb','document_id':'manual','chunk_index':idx,
         'text':f'vector text {idx}','score':.95-idx*.05}
        for idx in range(5)
    ]
    weak = [{'kb_id':'kb','document_id':'manual','chunk_index':99,
             'score':None,'text':text,'_lexical_weight':weight}]
    found = fuse_full_corpus(vectors, weak)
    assert [row['chunk_index'] for row in found[:5]] == list(range(5))
    assert found[0]['score'] == vectors[0]['score']


def test_dev11_exact_code_must_be_complete_not_a_substring():
    from pharos.knowledge.lexical import _identifiers, _has_identifier
    assert 'hjx-987' in _identifiers('Come funziona HJX-987?')
    assert '802.11' in _identifiers('Quali protocolli IEEE 802.11 sono supportati?')
    assert '2026' not in _identifiers('Eventi previsti nel 2026')
    assert _has_identifier('Codice HJX-987.', 'hjx-987')
    assert 'hjx-987' in _identifiers('Codice HJX-987.')
    assert _has_identifier('Protocollo 802.11.', '802.11')
    assert not _has_identifier('Protocollo 802.11.6', '802.11')
    assert not _has_identifier('Codice PRE.HJX-987.', 'hjx-987')
    assert not _has_identifier('Codice HJX-9876.', 'hjx-987')
    assert not _has_identifier('Codice PRE-HJX-987.', 'hjx-987')
    assert _lexical_strength('La variante HJX-9876', 'Cercare HJX-987',
                             ['hjx','987']) == 0.0


def test_dev11_exact_three_term_phrase_can_surface_missing_chunk():
    question = 'Where is the emergency shutdown procedure described?'
    passage = 'Follow the emergency shutdown procedure to power off safely.'
    weight = _lexical_strength(passage, question,
                               ['emergency','shutdown','procedure'])
    assert weight == 1.25
    vectors = [{'kb_id':'kb','document_id':'v','chunk_index':0,
                'score':0.91,'text':'unrelated but semantically nearby'}]
    lexical = [{'kb_id':'kb','document_id':'v','chunk_index':10,
                'score':None,'text':passage,'_lexical_weight':weight}]
    fused = fuse_full_corpus(vectors,lexical)
    assert fused[0]['chunk_index'] == 10
    assert fused[0]['score'] is None


def test_dev11_two_term_phrase_gently_reranks_but_cannot_displace_first():
    question = 'How to configure wireless network?'
    passage = 'Follow configure wireless network to connect.'
    weight = _lexical_strength(passage, question,
                               ['configure','wireless','network'])
    assert weight == 1.25
    weak_phrase = _lexical_strength('configure wireless is available',
                                    question,['configure','wireless'])
    assert weak_phrase == 0.035
    vectors = [
        {'kb_id':'kb','document_id':'d','chunk_index':i,
         'score':0.9-i*0.03,'text':'semantic'}
        for i in range(5)
    ]
    lex = [{'kb_id':'kb','document_id':'e','chunk_index':9,'score':None,
            'text':'configure wireless','_lexical_weight':weak_phrase}]
    assert fuse_full_corpus(vectors,lex)[0]['document_id'] == 'd'


def test_dev11_mixed_script_identifier_terms_cannot_be_overwritten_by_generic_or():
    with sqlite3.connect(':memory:') as db:
        db.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, tokenize='unicode61')")
        db.executemany('INSERT INTO chunks(text) VALUES (?)',
                       [('I protocolli 802.11 sono disponibili',)] +
                       [('Standard IEEE 802 supportato, modalità 11 attiva',)] * 20)
        selected = _distinctive_terms(db, 'Quali funzioni usa 802.11?')
        assert '802' in selected and '11' in selected
    assert _lexical_strength('I protocolli 802.11 sono disponibili',
                             'Quali funzioni usa 802.11?', selected) == 2.4
    assert _lexical_strength('Standard IEEE 802 supportato, modalità 11 attiva',
                             'Quali funzioni usa 802.11?', selected) < 0.1


def test_dev11_exact_protocol_surfaces_beyond_first_48_or_hits(tmp_path):
    """The independent phrase query must find an exact code on a long corpus."""
    engine = FakeEngine(tmp_path)
    engine.points = []
    for i in range(60):
        engine.points.append({'payload': {
            'kb_id': engine.kb_id, 'document_id': engine.doc_id,
            'filename': 'Manual.pdf', 'page': i+1, 'chunk_index': i,
            'text': 'Norma IEEE 802 applicabile; configurazione versione 11.'}})
    engine.points.append({'payload': {
        'kb_id': engine.kb_id, 'document_id': engine.doc_id,
        'filename': 'Manual.pdf', 'page': 141, 'chunk_index': 60,
        'text': 'Questo dispositivo supporta protocolli IEEE 802.11 a/b/g/n/ac.'}})
    with engine._db() as db:
        db.execute('UPDATE documents SET chunks=61')
    index = LexicalIndex(engine)
    asyncio.run(index.build(engine.kb_id, uuid4().hex, lambda *_: None))
    hits = index.search(engine.kb_id, 'Quali protocolli IEEE 802.11?', 5)
    assert hits and hits[0]['page'] == 141
    assert hits[0]['chunk_index'] == 60
    assert hits[0]['_lexical_weight'] == 2.4


def test_dev11_numeric_zero_prefixed_pin_is_exact_evidence():
    from pharos.knowledge.lexical import _identifiers
    assert _identifiers('PIN predefinito 0000') == ['0000']
    assert '2026' not in _identifiers('Anno 2026')
    assert _lexical_strength('Il PIN di default è 0000', 'PIN predefinito 0000',
                             ['pin','0000']) == 2.4
    assert _lexical_strength('Il PIN di default è 00001', 'PIN predefinito 0000',
                             ['pin','0000']) < 0.1


def test_dev11_weak_terms_cannot_replace_top_five_vector_chunks():
    vectors = [
        {'kb_id': 'kb', 'document_id':'manual', 'chunk_index':i,
         'score':round(0.9-i*.01,3), 'text':'semantic passage'}
        for i in range(20)
    ]
    lexical = [
        {'kb_id':'kb','document_id':'manual','chunk_index':50+i,
         'score':None, 'text':'troubleshooting update software',
         '_lexical_weight':0.008}
        for i in range(48)
    ]
    output=fuse_full_corpus(vectors,lexical)
    assert [h['chunk_index'] for h in output[:5]] == list(range(5))
    assert all(h['score'] is not None for h in output[:5])
