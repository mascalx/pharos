"""Local hermetic regression tests for the PHAROS RAG A/B comparison CLI."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'rag-compare.py'
SPEC = importlib.util.spec_from_file_location('pharos_rag_compare', SCRIPT)
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def test_exact_kb_and_multi_kb_guard():
    items = [{'id': '1', 'name': 'test'}, {'id': '2', 'name': 'Other'}]
    assert compare.find_kb(items, 'TEST')['id'] == '1'
    assert compare.find_kb(items, '2')['name'] == 'Other'
    with pytest.raises(ValueError, match='Multiple Knowledge Bases'):
        compare.find_kb(items, None)
    assert compare.find_kb(items[:1], None)['id'] == '1'


def test_input_validation():
    assert compare.trusted_origin('http://127.0.0.1/') == 'http://127.0.0.1'
    with pytest.raises(ValueError):
        compare.trusted_origin('https://someone:secret@example.org')
    with pytest.raises(ValueError):
        compare.trusted_origin('https://example.org/not-root')
    assert compare.parse_questions(['  Come si configura?  '], None) == ['Come si configura?']
    with pytest.raises(ValueError):
        compare.parse_questions([], None)


def test_evidence_key_distinguishes_pages_and_chunks():
    h = {'kb_id': 'kb', 'document_id': 'doc', 'page': 2, 'chunk_index': 4}
    g = dict(h, page=3)
    assert compare.evidence_key(h) != compare.evidence_key(g)
    assert compare.summarize_hit({'text':'  multiple\nspaces  ', 'score':0.6})['excerpt'] == 'multiple spaces'


def test_paired_search_keeps_results_and_alternates_order():
    calls = []

    def fake_request(origin, path, payload=None, timeout=90):
        assert path == 'rag/search'
        mode = payload['retrieval_mode']
        calls.append(mode)
        if mode == 'vector':
            return {'results': [
                {'kb_id':'kb', 'document_id':'doc', 'page':1, 'chunk_index':0,
                 'filename':'Samsung.pdf','text':'a','score':0.99},
                {'kb_id':'kb', 'document_id':'doc', 'page':2, 'chunk_index':1,
                 'filename':'Samsung.pdf','text':'b','score':0.95}]}
        return {'results': [
            {'kb_id':'kb', 'document_id':'doc', 'page':2, 'chunk_index':1,
             'filename':'Samsung.pdf','text':'b','score':0.95},
            {'kb_id':'kb', 'document_id':'doc', 'page':3, 'chunk_index':2,
             'filename':'Samsung.pdf','text':'c','score':0.92}]}

    with patch.object(compare, 'request_json', side_effect=fake_request):
        report = compare.run('http://127.0.0.1', {'id':'kb','name':'test'},
                             ['Domanda uno', 'Domanda due'], limit=2)
    assert calls == ['vector','candidate_fusion','candidate_fusion','vector']
    assert len(report['results']) == 2
    assert report['results'][0]['shared_top_k'] == 1
    assert report['results'][0]['top_hit_changed'] is True
    assert report['results'][0]['modes']['vector']['hits'][0]['page'] == 1
    assert report['results'][0]['modes']['candidate_fusion']['hits'][0]['page'] == 2
    assert '_keys' not in report['results'][0]['modes']['vector']


def test_answer_generation_only_when_explicitly_requested():
    calls = []

    def fake_request(origin, path, payload=None, timeout=90):
        calls.append((path, payload['retrieval_mode']))
        if path == 'rag/search':
            return {'results': []}
        assert path == 'rag/answer'
        assert payload['model'] == 'my-chat-model'
        return {'answer': 'Risposta [1]', 'sources': [{'reference': 1}],
                'citations_missing': False, 'insufficient_evidence': False}
    with patch.object(compare, 'request_json', side_effect=fake_request):
        report = compare.run('http://127.0.0.1', {'id':'kb','name':'test'},
                             ['Domanda'], answer_model='my-chat-model')
    assert calls == [('rag/search', 'vector'), ('rag/answer','vector'),
                     ('rag/search','candidate_fusion'),('rag/answer','candidate_fusion')]
    assert report['results'][0]['modes']['candidate_fusion']['answer'] == 'Risposta [1]'


def test_dev8_contextual_preview_is_used_without_discarding_source_metadata():
    hit = {'text': 'AirPlay section and a very long opening.',
           'preview': '… Ripristino televisore: Impostazioni > Generali > Reset.',
           'page': 128, 'chunk_index': 237, 'score': 0.68}
    shown = compare.summarize_hit(hit)
    assert shown['excerpt'].startswith('… Ripristino')
    assert shown['page'] == 128 and shown['chunk_index'] == 237
    assert shown['score'] == 0.68
    assert hit['text'].startswith('AirPlay')  # No in-place mutation.



def test_optional_third_arm_hybrid_fts_is_not_default():
    calls = []
    def fake_request(origin, path, payload=None, timeout=90.0):
        calls.append(payload['retrieval_mode'])
        return {'results': []}
    with patch.object(compare, 'request_json', side_effect=fake_request):
        report = compare.run('http://127.0.0.1', {'id': 'kb', 'name': 'test'},
                             ['Find code HJX-987'], include_hybrid=True)
    assert calls == ['vector', 'candidate_fusion', 'hybrid_fts']
    assert report['modes'] == ['vector', 'candidate_fusion', 'hybrid_fts']
    assert report['results'][0]['modes']['hybrid_fts']['hits'] == []


def test_dev10_each_mode_comparison_and_no_private_keys():
    def fake_request(origin, path, payload=None, timeout=90):
        mode = payload['retrieval_mode']
        ranks = {
            'vector': [(9, 12), (10, 13)],
            'candidate_fusion': [(10, 13), (9, 12)],
            'hybrid_fts': [(141, 266), (9, 12)],
        }
        return {'results':[{'kb_id':'kb','document_id':'manual','page':page,
                            'chunk_index':chunk,'text':'sample'}
                           for page, chunk in ranks[mode]]}
    with patch.object(compare,'request_json',side_effect=fake_request):
        result=compare.run('http://127.0.0.1', {'id':'kb','name':'test'},
                           ['Come si configura Wi-Fi?'], limit=2,include_hybrid=True)
    row=result['results'][0]
    assert row['comparisons']['candidate_fusion']['shared_top_k'] == 2
    assert row['comparisons']['hybrid_fts']['shared_top_k'] == 1
    assert row['comparisons']['hybrid_fts']['top_hit_changed']
    assert row['shared_top_k'] == 2  # Legacy compatibility.
    assert all('_keys' not in value for value in row['modes'].values())


def test_dev10_passage_relevance_metrics_not_document_only(tmp_path):
    relevant=[{'document_id':'manual','page':9,'chunk_index':12},
              {'document_id':'manual','page':10,'chunk_index':13}]
    hits=[{'document_id':'manual','page':141,'chunk_index':266},
          {'document_id':'manual','page':9,'chunk_index':12}]
    metrics=compare.evaluate_passages(hits,relevant)
    assert metrics=={'recall_at_k':0.5, 'reciprocal_rank':0.5, 'first_relevant_rank':2}

    truth=[{'question':'Come si configura Wi-Fi?', 'relevant':relevant}]
    local=tmp_path/'ground-truth.json'
    import json
    local.write_text(json.dumps(truth),encoding='utf-8')
    assert compare.load_ground_truth(local)[truth[0]['question']] == relevant

    with patch.object(compare,'request_json',return_value={'results':hits}):
        report=compare.run('http://127.0.0.1', {'id':'kb','name':'test'},
                           ['Come si configura Wi-Fi?'],limit=2,
                           truth=compare.load_ground_truth(local),include_hybrid=True)
    assert all(hit['relevance']['recall_at_k']==0.5
               for hit in report['results'][0]['modes'].values())
    assert all(metric['mean_recall_at_k'] == 0.5
               for metric in report['relevance_summary'].values())


def test_dev10_reject_invalid_ground_truth_without_silently_guessing(tmp_path):
    import json
    data=[{'question':'Q', 'relevant':[{'page':'9'}]}]
    file=tmp_path/'truth.json'
    file.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='Invalid ground truth'):
        compare.load_ground_truth(file)
    file.write_text(json.dumps([{'question':'Q','relevant':[{'page':9}]},
                                {'question':'Q','relevant':[{'page':10}]}]),encoding='utf-8')
    with pytest.raises(ValueError,match='unique'):
        compare.load_ground_truth(file)


def test_dev10_missing_truth_rejected_before_any_network_calls():
    with patch.object(compare, 'request_json') as request:
        with pytest.raises(ValueError, match='Missing ground truth'):
            compare.run('http://127.0.0.1', {'id':'kb','name':'test'},
                        ['Unknown query'],truth={'Another question':[{'page':2}]})
    request.assert_not_called()
