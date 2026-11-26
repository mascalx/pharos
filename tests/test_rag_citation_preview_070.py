"""PHAROS 0.7.0.dev8: citation integrity and non-destructive source previews."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pharos.knowledge.citations import normalize_answer_citations
from pharos.knowledge.excerpts import relevant_excerpt
from pharos.knowledge.rag import RAGEngine, _with_previews


def test_grouped_citations_return_each_real_source():
    text, cited, missing = normalize_answer_citations('Procedura [1, 7] e controllo [2].', 8)
    assert text == 'Procedura [1] [7] e controllo [2].'
    assert cited == {1, 2, 7}
    assert missing is False


def test_citation_out_of_range_never_becomes_evidence():
    text, cited, missing = normalize_answer_citations('Verificare [1, 99] e [999].', 4)
    assert text == 'Verificare [1] e .'
    assert cited == {1}
    assert missing is True


def test_ranges_and_duplicates():
    text, cited, missing = normalize_answer_citations('[2-4] poi [2; 4] e [3–4].', 5)
    assert '[2] [3] [4]' in text
    assert cited == {2, 3, 4} and not missing


@pytest.mark.parametrize('bad', ['[9-1]', '[1-55]', '[99]', '[1,]'])
def test_bad_citation_labels_report_missing(bad):
    _, cited, missing = normalize_answer_citations('Test '+bad, 8)
    assert not cited and missing


def test_non_numeric_brackets_preserved_and_unrecognized_labels_warn():
    text, cited, missing = normalize_answer_citations('Etichetta [nota] e [1, abc] poi [1].', 3)
    assert '[nota]' in text and '[1, abc]' in text
    assert cited == {1} and missing
    assert normalize_answer_citations('Test [note]', 2)[2] is True
    with pytest.raises(ValueError):
        normalize_answer_citations('Test', -1)


def test_preview_prefers_reset_section_in_mixed_manual_page():
    manual_page = ('Uso di AirPlay. Impostazioni Generali Apple AirPlay. '
                   + 'Note sui dispositivi di rete. ' * 11
                   + 'Ripristino del televisore ai valori di fabbrica. '
                   + 'Impostazioni Generali Reset. Ripristinare le impostazioni. ' * 2)
    preview = relevant_excerpt(manual_page, 'Come si ripristinano le impostazioni del televisore?')
    assert 'Ripristino del televisore' in preview
    assert preview.startswith('… ')
    assert len(preview) <= 234


def test_preview_does_not_hide_wifi_menu_when_overlap_is_generic():
    manual_page = ('Stabilire una connessione Internet wireless. Impostazioni Generali Rete '
                   'Apri Impostazioni di rete Wireless. ' + 'Dettagli di configurazione. '*40)
    preview = relevant_excerpt(manual_page, 'Come si configura il collegamento Wi-Fi?')
    assert preview.startswith('Stabilire una connessione Internet wireless')


def test_source_preview_retains_original_text_and_metadata():
    full_text = 'AirPlay e impostazioni. '*15 + 'Ripristino televisore impostazioni Reset. '
    original = [{'text': full_text, 'document_id': 'doc', 'page': 128, 'score': 0.61}]
    with_preview = _with_previews(original, 'Ripristino impostazioni televisore')
    assert original[0].get('preview') is None
    assert with_preview[0]['text'] == original[0]['text']
    assert with_preview[0]['page'] == 128
    assert with_preview[0]['score'] == 0.61
    assert 'Ripristino' in with_preview[0]['preview']
    assert _with_previews([{'source_type': 'zim', 'title': 'No content'}], 'test')[0]['title'] == 'No content'


def test_preview_handles_unicode_and_short_text():
    assert relevant_excerpt(' 地图 \n маршрут ', 'маршрут') == '地图 маршрут'
    with pytest.raises(ValueError):
        relevant_excerpt('test', 'x', 40)


def test_real_answer_adapter_expands_grouped_citations_without_inventing_sources():
    engine = RAGEngine.__new__(RAGEngine)
    async def fake_search(*args, **kwargs):
        assert kwargs['retrieval_mode'] == 'vector'
        return [{'text': f'Passage number {i}.', 'page': i, 'filename': 'manual.pdf'}
                for i in range(1, 8)]
    async def fake_expand(hits):
        return hits
    async def fake_complete(model, messages):
        return 'Il percorso è corretto [1, 7] ma va verificato [99].'
    engine.search = fake_search
    engine._expand_context = fake_expand
    engine.gateway = SimpleNamespace(complete=fake_complete)
    result = asyncio.run(engine.answer(['kb'], 'Dove?', 'test-model'))
    assert result['answer'] == 'Il percorso è corretto [1] [7] ma va verificato .'
    assert [row['reference'] for row in result['sources']] == [1, 7]
    assert result['citations_missing'] is True
    assert not result['insufficient_evidence']
