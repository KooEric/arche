#!/usr/bin/env python3
"""Create blinded synthetic-study review packets and grade reviewer responses."""
import argparse
from collections import defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import random
import re

import repair

STUDIES = Path(__file__).resolve().parent / 'studies'
VERSION = 1
IDENTIFIER = re.compile(r'^[A-Za-z0-9_./:-]{1,160}$')
EXPRESSION = re.compile(r'^[A-Za-z0-9_ .()!<>=&|+*/%\-]{1,200}$')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def cases():
    manifest = read(STUDIES / 'manifest.json')
    if manifest.get('version') != VERSION:
        raise ValueError('unsupported study manifest version')
    result = {c['id']: dict(c, kind='study') for c in manifest['cases']}
    for case, (folder, prompt) in repair.CASES.items():
        result[case] = {'id': case, 'kind': 'repair', 'folder': folder, 'question': prompt}
    return result


def clean_string(value, pattern=IDENTIFIER):
    return value if isinstance(value, str) and pattern.fullmatch(value) else '[redacted]'


def candidate(value):
    if not isinstance(value, dict):
        return None
    answer = {}
    for field, keys in [('steps', ('id',)), ('edges', ('from', 'to', 'kind')), ('conditions', ('id', 'expression'))]:
        items = value.get(field, [])
        if not isinstance(items, list) or len(items) > 1000:
            raise ValueError('invalid candidate ' + field)
        answer[field] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('invalid candidate item')
            clean = {k: clean_string(item.get(k), EXPRESSION if k == 'expression' else IDENTIFIER) for k in keys}
            if 'citations' in item:
                citations = item['citations']
                if not isinstance(citations, list) or len(citations) > 1000:
                    raise ValueError('invalid citations')
                clean['citations'] = [{'path': clean_string(c.get('path')), 'line': c.get('line') if type(c.get('line')) is int and 0 < c['line'] < 100000 else None} for c in citations if isinstance(c, dict)]
            answer[field].append(clean)
    return answer


def sample(comparison, count, seed, output):
    doc = read(comparison)
    if not isinstance(doc, dict) or doc.get('schema_version') != VERSION or not isinstance(doc.get('trials'), list):
        raise ValueError('unsupported comparison schema')
    known = cases()
    groups = defaultdict(list)
    for index, row in enumerate(doc['trials']):
        if not isinstance(row, dict) or row.get('case') not in known:
            continue
        kind = known[row['case']]['kind']
        if kind == 'study' and not isinstance(row.get('candidate'), dict):
            continue
        if kind == 'repair' and not isinstance(row.get('evidence'), dict):
            continue
        groups[row['case']].append((index, row))
    if count < 1 or count > sum(map(len, groups.values())):
        raise ValueError('count exceeds available synthetic trials')
    rng = random.Random(seed)
    for key in sorted(groups):
        rng.shuffle(groups[key])
    chosen = []
    while len(chosen) < count:
        for case in sorted(groups):
            if groups[case] and len(chosen) < count:
                chosen.append((case, *groups[case].pop()))
    rng.shuffle(chosen)
    packet, key = [], []
    for number, (case, index, row) in enumerate(chosen, 1):
        item = known[case]
        base = STUDIES / 'fixtures' if item['kind'] == 'study' else repair.ROOT / 'fixtures'
        fixture = (STUDIES / item['repo'] if item['kind'] == 'study' else base / item['folder']).resolve()
        if not fixture.is_relative_to(base.resolve()):
            raise ValueError('invalid fixture path')
        evidence = {p.name: p.read_text(encoding='utf-8') for p in sorted(fixture.iterdir()) if p.is_file() and not p.is_symlink()}
        if item['kind'] == 'study':
            answer = candidate(row['candidate'])
        else:
            answer = {}
            for name, diff in row['evidence'].items():
                if name not in evidence or not isinstance(diff, str) or len(diff) > 100000:
                    continue
                # Keep only diff lines and redact non-code-like or very long content.
                answer[name] = '\n'.join(line if len(line) <= 300 and re.fullmatch(r'[ +@\-A-Za-z0-9_ .(){}\[\]:;,=<>!&|+*/%\"\'`?#\\]*', line) else '[redacted]' for line in diff.splitlines())
        pseudonym = f'R{number:04d}'
        packet.append({'id': pseudonym, 'case': case, 'task': item['question'], 'candidate': answer, 'evidence': evidence})
        key.append({'id': pseudonym, 'comparison_index': index, 'case': case, 'trial': row.get('trial'), 'model': row.get('model'), 'arm': row.get('arm'), 'automated_outcome': row.get('outcome')})
    public = {'schema_version': VERSION, 'items': packet}
    private = {'schema_version': VERSION, 'packet_sha256': hashlib.sha256(encoded(public)).hexdigest(), 'seed': seed, 'trials': key}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'packet.json').write_bytes(encoded(public))
    (output / 'key.json').write_bytes(encoded(private))


def grade(sample_dir, responses_file, output):
    directory = Path(sample_dir)
    packet, key, response = read(directory / 'packet.json'), read(directory / 'key.json'), read(responses_file)
    if any(not isinstance(d, dict) or d.get('schema_version') != VERSION for d in (packet, key, response)):
        raise ValueError('unsupported review schema')
    if hashlib.sha256(encoded(packet)).hexdigest() != key.get('packet_sha256'):
        raise ValueError('packet does not match key')
    trials, items, responses = key.get('trials'), packet.get('items'), response.get('responses')
    if not all(isinstance(x, list) for x in (trials, items, responses)):
        raise ValueError('invalid review lists')
    ids = [x['id'] for x in items]
    if len(ids) != len(set(ids)) or [x['id'] for x in trials] != ids:
        raise ValueError('invalid packet IDs')
    reviewers = response.get('reviewers')
    if not isinstance(reviewers, list) or not reviewers or any(not isinstance(r, str) or not r for r in reviewers) or len(reviewers) != len(set(reviewers)):
        raise ValueError('reviewers must be unique nonempty strings')
    votes = {}
    for r in responses:
        if not isinstance(r, dict) or r.get('reviewer') not in reviewers or r.get('id') not in ids or type(r.get('correct')) is not bool:
            raise ValueError('invalid response')
        k = (r['id'], r['reviewer'])
        if k in votes:
            raise ValueError('duplicate response')
        votes[k] = r['correct']
    results, pairs, auto_pairs = [], [], []
    for trial in trials:
        ident = trial['id']
        judgements = {r: votes.get((ident, r)) for r in reviewers}
        available = [v for v in judgements.values() if v is not None]
        pairs.extend(a == b for a, b in itertools.combinations(available, 2))
        auto = {'passed': True, 'failed': False}.get(trial.get('automated_outcome'))
        if auto is not None:
            auto_pairs.extend(v == auto for v in available)
        results.append({'id': ident, 'judgements': judgements, 'automated_correct': auto})
    def agreement(values):
        return {'matches': sum(values), 'comparisons': len(values), 'rate': sum(values) / len(values) if values else None}
    result = {'schema_version': VERSION, 'results': results, 'missing_responses': sum(v is None for x in results for v in x['judgements'].values()), 'reviewer_agreement': agreement(pairs), 'automated_agreement': agreement(auto_pairs)}
    with Path(output).open('xb') as stream:
        stream.write(encoded(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('sample')
    p.add_argument('--comparison', required=True); p.add_argument('--count', type=int, required=True); p.add_argument('--seed', type=int, required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('grade')
    p.add_argument('--sample', required=True); p.add_argument('--responses', required=True); p.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'sample': sample(args.comparison, args.count, args.seed, args.output)
        else: grade(args.sample, args.responses, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, 'review error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
