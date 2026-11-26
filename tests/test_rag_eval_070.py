"""Offline unit checks for the read-only RAG retrieval benchmark (0.7.0.dev4)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pharos_rag_eval", ROOT / "scripts/rag-eval.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_rag_metrics_count_unique_documents_and_unfound_expectations():
    hits = [{"document_id": "other"}, {"document_id": "other"},
            {"document_id": "wanted"}, {"document_id": "wanted"}]
    result = benchmark.score_case(hits, ["wanted", "missing"], 4)
    assert result["recall"] == 0.5
    assert result["reciprocal_rank"] == 0.5
    assert result["found"] == 1
    assert result["expected"] == 2


def test_rag_metrics_empty_hit_set():
    assert benchmark.score_case([], ["wanted"], 5)["reciprocal_rank"] == 0.0


def test_benchmark_only_uses_read_only_search_and_aggregates():
    cases = [{"question": "How does it work?", "kb_ids": ["kb-1"],
              "relevant_document_ids": ["wanted"]},
             {"question": "Where is it?", "kb_ids": ["kb-1"],
              "relevant_document_ids": ["unfound"]}]
    with patch.object(benchmark, "fetch_results", side_effect=[
        [{"document_id": "wanted"}], [{"document_id": "other"}],
    ]) as fetch:
        result = benchmark.run(cases, "http://127.0.0.1:8080", 5, 10)
    assert fetch.call_count == 2
    assert result["cases"] == 2
    assert result["mean_recall"] == 0.5
    assert result["mrr"] == 0.5
    assert not any("question" in row for row in result["details"])


def test_nonempty_ground_truth_is_required():
    import pytest
    with pytest.raises(ValueError):
        benchmark.score_case([], [], 5)
