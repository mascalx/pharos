#!/usr/bin/env python3
"""Non-destructive, paired RAG evidence inspection on a running PHAROS instance.

No index or document changes. Uses only KB inventory, RAG search and optionally
RAG answer. Runs entirely on the machine that invokes it, using Python stdlib.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

MODES = ('vector', 'candidate_fusion')
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    """Do not send questions or source text to a redirected API origin."""
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected redirect from local PHAROS API')


# Ignore HTTP_PROXY/HTTPS_PROXY set by the invoking shell.
LOCAL_OPENER = build_opener(ProxyHandler({}), NoRedirect())


def trusted_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Use a trusted PHAROS origin without path or credentials')
    return value.rstrip('/')


def request_json(origin: str, path: str, payload: dict | None = None,
                 timeout: float = 90.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    headers = {'Accept': 'application/json'}
    if data is not None:
        headers.update({'Content-Type': 'application/json', 'X-Pharos-Request': '1'})
    request = Request(origin + '/api/' + path, data=data, headers=headers,
                      method='POST' if data is not None else 'GET')
    with LOCAL_OPENER.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('PHAROS response exceeds 4 MiB safety limit')
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('Unexpected JSON object from PHAROS')
    return body


def find_kb(items: list[dict], selector: str | None) -> dict:
    if not items:
        raise ValueError('No Knowledge Bases found')
    if not selector:
        if len(items) == 1:
            return items[0]
        names = ', '.join(f'{kb.get("name", "?")} ({kb.get("id", "?")})' for kb in items[:12])
        raise ValueError('Multiple Knowledge Bases found: specify --kb NAME or UUID. Available: ' + names)
    matches = [kb for kb in items if kb.get('id') == selector or
               str(kb.get('name', '')).casefold() == selector.casefold()]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one KB matching {selector!r}; found {len(matches)}')
    return matches[0]


def evidence_key(hit: dict) -> tuple:
    if hit.get('source_type') == 'zim':
        return ('zim', str(hit.get('filename', '')), str(hit.get('url', '')))
    return ('document', str(hit.get('kb_id', '')), str(hit.get('document_id', '')),
            hit.get('chunk_index'), hit.get('page'))


def summarize_hit(hit: dict) -> dict:
    # dev8 Core sends a question-centered preview; older Cores keep the legacy fallback.
    excerpt = re.sub(r'\s+', ' ', str(hit.get('preview') or hit.get('text', ''))).strip()[:230]
    value = {'source': hit.get('source_type') or 'document',
             'document_id': hit.get('document_id'),
             'filename': hit.get('filename', ''),
             'page': hit.get('page'),
             'chunk_index': hit.get('chunk_index'),
             'score': hit.get('score'),
             'excerpt': excerpt}
    if hit.get('source_type') == 'zim':
        value['article'] = hit.get('title', '')
    return value


def evaluate_passages(hits: list[dict], relevant: list[dict]) -> dict:
    """Evaluate annotated passages, not merely whether a PDF was retrieved."""
    ranks = []
    for target in relevant:
        rank = next((i for i, hit in enumerate(hits, 1)
                     if all(hit.get(k) == value for k, value in target.items())),
                    None)
        if rank is not None:
            ranks.append(rank)
    return {'recall_at_k': round(len(ranks) / len(relevant), 4),
            'reciprocal_rank': round(1 / min(ranks), 4) if ranks else 0.0,
            'first_relevant_rank': min(ranks) if ranks else None}


def load_ground_truth(file: Path) -> dict[str, list[dict]]:
    """Read explicit, local passage annotations; never guess correct pages."""
    data = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(data, list) or not data:
        raise ValueError('Ground truth must be a nonempty JSON array')
    result = {}
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get('question'), str):
            raise ValueError('Ground truth needs a question per item')
        question = entry['question'].strip()
        sources = entry.get('relevant')
        if not question or question in result or not isinstance(sources, list) or not sources:
            raise ValueError('Ground truth questions must be unique and annotated')
        cleaned = []
        for source in sources:
            if not isinstance(source, dict) or not source or not set(source) <= {
                'document_id', 'page', 'chunk_index', 'source'
            } or not ('page' in source or 'chunk_index' in source):
                raise ValueError('Ground truth requires a page or chunk_index')
            if any(type(source[key]) is not int or source[key] < (1 if key == 'page' else 0)
                   for key in ('page','chunk_index') if key in source):
                raise ValueError('Invalid ground truth page or chunk_index')
            if any(not isinstance(source[key], str) or not source[key].strip()
                   for key in ('document_id','source') if key in source):
                raise ValueError('Invalid ground truth document/source')
            if source in cleaned:
                raise ValueError('Duplicate ground truth source')
            cleaned.append(source)
        result[question] = cleaned
    return result


def run(origin: str, kb: dict, questions: list[str], *, limit: int = 5,
        answer_model: str = '', timeout: float = 90.0,
        include_hybrid: bool = False, truth: dict[str, list[dict]] | None = None) -> dict:
    if truth is not None:
        unknown = [question for question in questions if question not in truth]
        if unknown:
            raise ValueError('Missing ground truth for question: ' + unknown[0])
    results = []
    modes = MODES + (('hybrid_fts',) if include_hybrid else ())
    for index, question in enumerate(questions):
        cases = {}
        # Alternate order to reduce systematic warm-cache bias in timings.
        order = modes if index % 2 == 0 else modes[::-1]
        for mode in order:
            payload = {'kb_ids': [kb['id']], 'question': question,
                       'limit': limit, 'retrieval_mode': mode}
            started = time.monotonic()
            body = request_json(origin, 'rag/search', payload, timeout)
            search_ms = round(1000 * (time.monotonic() - started), 1)
            hits = body.get('results')
            if not isinstance(hits, list):
                raise ValueError('Missing RAG search results')
            case = {'search_ms': search_ms, 'hits': [summarize_hit(h) for h in hits[:limit]],
                    '_keys': [evidence_key(h) for h in hits[:limit]]}
            if answer_model:
                answer_payload = {**payload, 'model': answer_model}
                started = time.monotonic()
                answer = request_json(origin, 'rag/answer', answer_payload, timeout)
                case['answer_ms'] = round(1000 * (time.monotonic() - started), 1)
                case['answer'] = answer.get('answer', '')
                case['sources'] = [dict(source) for source in answer.get('sources', [])]
                case['insufficient_evidence'] = bool(answer.get('insufficient_evidence'))
                case['citations_missing'] = bool(answer.get('citations_missing'))
            cases[mode] = case
        keys = {mode: cases[mode].pop('_keys') for mode in modes}
        comparisons = {}
        for mode in modes:
            if mode == 'vector':
                continue
            comparisons[mode] = {
                'shared_top_k': len(set(keys['vector']) & set(keys[mode])),
                'top_hit_changed': bool(keys['vector'] and keys[mode] and
                                        keys['vector'][0] != keys[mode][0])
            }
        if truth is not None:
            if question not in truth:
                raise ValueError('Missing ground truth for question: ' + question)
            for mode in modes:
                cases[mode]['relevance'] = evaluate_passages(cases[mode]['hits'], truth[question])
        # Legacy fields retain their original vector-vs-candidate_fusion meaning.
        old = comparisons['candidate_fusion']
        results.append({'question': question, 'modes': cases, 'comparisons': comparisons,
                        'shared_top_k': old['shared_top_k'],
                        'top_hit_changed': old['top_hit_changed']})
    report = {'kb_name': kb['name'], 'kb_id': kb['id'], 'limit': limit,
              'modes': list(modes),
              'answer_model': answer_model or None, 'results': results}
    if truth is not None:
        report['relevance_summary'] = {
            mode: {
                'mean_recall_at_k': round(
                    sum(row['modes'][mode]['relevance']['recall_at_k'] for row in results)
                    / len(results), 4),
                'mrr': round(
                    sum(row['modes'][mode]['relevance']['reciprocal_rank'] for row in results)
                    / len(results), 4),
            } for mode in modes
        }
    return report


def print_report(report: dict) -> None:
    print(f'PHAROS RAG A/B — KB {report["kb_name"]} | top {report["limit"]}')
    if report['answer_model']:
        print('Optional generation model:', report['answer_model'])
    for index, row in enumerate(report['results'], 1):
        print(f'\n=== {index}. {row["question"]} ===')
        for mode, comparison in row.get('comparisons', {}).items():
            print(f'  vs vector / {mode}: shared '
                  f'{comparison["shared_top_k"]}/{report["limit"]}; '
                  f'first hit changed: {"yes" if comparison["top_hit_changed"] else "no"}')
        for mode in report.get('modes', MODES):
            value = row['modes'][mode]
            print(f'\n  {mode}: search {value["search_ms"]:.1f} ms')
            if not value['hits']:
                print('    (no evidence)')
            for rank, hit in enumerate(value['hits'], 1):
                location = ('article ' + hit.get('article', '')) if hit['source'] == 'zim' else \
                           ('page ' + str(hit['page']) + ', chunk ' + str(hit['chunk_index']))
                score = f'{hit["score"]:.5f}' if isinstance(hit['score'], (float, int)) else 'n/a'
                print(f'    {rank}. {hit["filename"]} | {location} | Qdrant score {score}')
                print('       ' + hit['excerpt'])
            if 'relevance' in value:
                metric = value['relevance']
                print(f'    Passage recall@K: {metric["recall_at_k"]:.2f}; '
                      f'first relevant rank: {metric["first_relevant_rank"]}; '
                      f'MRR: {metric["reciprocal_rank"]:.2f}')
            if 'answer' in value:
                print(f'    Generation ({value["answer_ms"]:.1f} ms): {value["answer"]}')
                print('    Citations:', len(value['sources']),
                      '| insufficient:', value['insufficient_evidence'],
                      '| missing:', value['citations_missing'])
    if report.get('relevance_summary'):
        print('\nGround-truth passage metrics (mean across annotated questions):')
        for mode, metric in report['relevance_summary'].items():
            print(f'  {mode}: recall@K={metric["mean_recall_at_k"]:.3f}, '
                  f'MRR={metric["mrr"]:.3f}')
    print('\nThe script does not determine factual correctness: check passages against the original PDF.')


def parse_questions(inline: list[str], file: Path | None) -> list[str]:
    questions = [text.strip() for text in inline]
    if file is not None:
        questions.extend(line.strip() for line in file.read_text(encoding='utf-8').splitlines()
                         if line.strip() and not line.lstrip().startswith('#'))
    if not 1 <= len(questions) <= 20 or any(not q or len(q) > 2000 for q in questions):
        raise ValueError('Supply 1–20 nonempty questions, each at most 2000 characters')
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1',
                        help='Trusted PHAROS origin, by default local NGINX port 80')
    parser.add_argument('--kb', help='Exact KB name or UUID; inferred if only one KB exists')
    parser.add_argument('--question', action='append', default=[], help='Repeat for multiple questions')
    parser.add_argument('--questions-file', type=Path, help='UTF-8 file, one question per line')
    parser.add_argument('--limit', type=int, default=5, choices=range(1, 13), metavar='1..12')
    parser.add_argument('--answers', action='store_true', help='Also ask Tiiny for paired answers (2 per question)')
    parser.add_argument('--include-hybrid', action='store_true',
                        help='Also compare opt-in full-corpus hybrid_fts (requires a ready FTS5 index)')
    parser.add_argument('--model', help='Generative model override, used only with --answers')
    parser.add_argument('--timeout', type=float, default=90.0)
    parser.add_argument('--json-out', type=Path, help='Save local evidence and answers as JSON')
    parser.add_argument('--truth-file', type=Path,
                        help='Optional local JSON ground truth by question and relevant page/chunk')
    args = parser.parse_args()
    try:
        origin = trusted_origin(args.base_url)
        if args.timeout <= 0:
            raise ValueError('Timeout must be positive')
        if args.model and not args.answers:
            raise ValueError('--model requires --answers')
        questions = parse_questions(args.question, args.questions_file)
        data = request_json(origin, 'kb', timeout=args.timeout)
        kb = find_kb(data.get('items', []), args.kb)
        model = args.model or ''
        if args.answers and not model:
            settings = request_json(origin, 'settings', timeout=args.timeout)
            model = str(settings.get('values', {}).get('ai', {}).get('default_model', '')).strip()
            if not model:
                raise ValueError('No default chat model configured; supply --model MODEL')
        truth = load_ground_truth(args.truth_file) if args.truth_file else None
        result = run(origin, kb, questions, limit=args.limit, answer_model=model,
                     timeout=args.timeout, include_hybrid=args.include_hybrid,
                     truth=truth)
        print_report(result)
        if args.json_out is not None:
            args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('JSON report written to:', args.json_out)
        return 0
    except (ValueError, OSError, KeyError, TypeError, URLError, HTTPError, json.JSONDecodeError) as error:
        print('RAG comparison failed:', error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
