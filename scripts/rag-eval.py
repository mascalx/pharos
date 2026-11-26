#!/usr/bin/env python3
"""Read-only RAG retrieval benchmark against an existing PHAROS installation.

Input is a locally curated JSON array of cases with question, kb_ids and
relevant_document_ids. The tool calls only POST /api/rag/search and does not
modify KBs, documents, indexes or model settings. No answer generation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def score_case(results: list[dict], relevant: list[str], cutoff: int) -> dict:
    """Document-level recall and reciprocal rank; duplicate chunks count once."""
    if not relevant:
        raise ValueError("Each case needs at least one relevant document ID")
    if cutoff < 1 or cutoff > 12:
        raise ValueError("cutoff must be between 1 and 12")
    ranked = []
    for hit in results[:cutoff]:
        doc_id = hit.get("document_id")
        if isinstance(doc_id, str) and doc_id not in ranked:
            ranked.append(doc_id)
    expected = set(relevant)
    found = expected.intersection(ranked)
    first_rank = next((i for i, doc in enumerate(ranked, 1) if doc in expected), None)
    return {"recall": len(found) / len(expected),
            "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
            "found": len(found), "expected": len(expected)}


def fetch_results(base_url: str, case: dict, cutoff: int, timeout: float,
                  mode: str = 'vector') -> list[dict]:
    payload = json.dumps({"kb_ids": case["kb_ids"],
                          "question": case["question"], "limit": cutoff,
                          "retrieval_mode": mode}).encode("utf-8")
    request = Request(base_url.rstrip("/") + "/api/rag/search", data=payload,
                      headers={"Content-Type": "application/json", "X-Pharos-Request": "1"},
                      method="POST")
    with urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValueError("Unexpected PHAROS RAG search response")
    return body["results"]


def run(cases: list[dict], base_url: str, cutoff: int, timeout: float,
        mode: str = 'vector') -> dict:
    if not cases:
        raise ValueError("At least one benchmark case is required")
    if mode not in ("vector", "candidate_fusion", "hybrid_fts"):
        raise ValueError("Unknown retrieval mode")
    scores = []
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict) or not isinstance(case.get("question"), str) or \
                not isinstance(case.get("kb_ids"), list) or \
                not isinstance(case.get("relevant_document_ids"), list):
            raise ValueError(f"Invalid benchmark case {index}")
        started = time.monotonic()
        hits = fetch_results(base_url, case, cutoff, timeout, mode)
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        metrics = score_case(hits, case["relevant_document_ids"], cutoff)
        scores.append({"case": index, "latency_ms": duration_ms, **metrics})
    count = len(scores)
    return {"cases": count, "cutoff": cutoff, "mode": mode,
            "mean_recall": round(sum(x["recall"] for x in scores) / count, 4),
            "mrr": round(sum(x["reciprocal_rank"] for x in scores) / count, 4),
            "mean_latency_ms": round(sum(x["latency_ms"] for x in scores) / count, 1),
            "details": scores}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Local UTF-8 JSON case file")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080",
                        help="PHAROS origin; use an explicitly trusted LAN endpoint")
    parser.add_argument("--cutoff", type=int, default=5, choices=range(1, 13), metavar="1..12")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--mode", choices=("vector", "candidate_fusion", "hybrid_fts"), default="vector")
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("case file must be a JSON array")
        print(json.dumps(run(cases, args.base_url, args.cutoff, args.timeout, args.mode), indent=2))
    except (OSError, ValueError, KeyError, HTTPError, URLError) as exc:
        print(f"RAG benchmark failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
