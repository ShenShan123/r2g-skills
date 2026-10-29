"""Frozen SOURCE representations and five Table 2 memory adapters.

No TEST write-back. Construction calls are supplied by the governed runner.
External algorithms use the pinned reference mechanisms; domain interfaces and
their deviations are recorded in the adapter specification.
"""
from __future__ import annotations
import ast
from collections import defaultdict
import hashlib
import json
import os
import sys
from pathlib import Path
import random
import re

SOURCE_SHA = '6d08a2e68e4900fad815d2d6ba167899f022def81c9a5132a0541d3c322faa73'
ROOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/external-methods')
_PRIVATE = Path(__file__).resolve().parents[2] / 'upstream'
REFERENCES = _PRIVATE if _PRIVATE.is_dir() else ROOT
GROUP_KEYS = ('platform', 'check', 'violation_class')
RECIPE_KEYS = (*GROUP_KEYS, 'die_mode', 'util_band', 'timeout')


def stable(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(stable(value).encode()).hexdigest()


def load_source(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('SOURCE-v1 digest mismatch')
    units = [json.loads(line) for line in raw.splitlines()]
    if len(units) != 1933 or len({u['unit_id'] for u in units}) != len(units):
        raise ValueError('SOURCE-v1 unit count/identity mismatch')
    return units


def situation(unit):
    return dict(unit['raw_row'].get('situation') or {})


def load_shared(directory):
    """The shared history package (memory/scripts/t2_shared_history.py): the same units and the
    same labels for every arm. The manifest's units hash must match (fail closed)."""
    directory = Path(directory)
    raw = (directory / 'shared_units.jsonl').read_bytes()
    want = json.loads((directory / 'manifest.json').read_text()).get('units_sha256')
    if hashlib.sha256(raw).hexdigest() != want:
        raise ValueError('shared history package digest mismatch')
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def positive(unit):
    # Shared package: ann-v2 admission is the ONLY success signal for every arm (user review
    # 2026-10-18); the export's enriched rollback/outcome fields are not evidence.
    if 'shared_label' in unit:
        return unit['shared_label'].get('admitted') is True
    r = unit['rollback']
    retained = r.get('rolled_back') is False or str(r.get('rolled_back')).lower() == 'false'
    retained |= (r.get('recovery_applied') or {}).get('status_for_main') == 'false'
    return (unit.get('source_eligible') is True and unit['outcome'].get('verified') is True
            and unit['outcome'].get('canonical') == 'PASS' and retained)


def task_key(unit):
    x = unit['lineage']
    return stable([x['design'], x['platform'], x['project']])


def trajectories(units):
    buckets = defaultdict(list)
    for u in units:
        buckets[(task_key(u), u.get('episode_trace_id', u['identity']['session']))].append(u)
    out = []
    for (task, session), rows in sorted(buckets.items()):
        rows.sort(key=lambda u: (u['capture_context'].get('step_index', 0), u['unit_id']))
        success = any(positive(u) for u in rows)
        outcomes = {u['episode_status'] for u in rows if 'episode_status' in u}
        if outcomes:
            if len(outcomes) != 1 or not outcomes <= {'ACCEPTED', 'NOT_ACCEPTED'}:
                raise ValueError('contradictory or incomplete episode trajectory')
            # Whole-trajectory success is NOT a single cleared intermediate check.
            success = outcomes == {'ACCEPTED'}
        out.append({'id': fingerprint([task, session]), 'task': task, 'session': session,
                    'success': success, 'text': '\n'.join(u['text_view'] for u in rows),
                    'units': [u['unit_id'] for u in rows], 'situation': situation(rows[0]),
                    'time': min(u['identity'].get('ts') or '' for u in rows)})
    return out


def expel_operations():
    """Execute only the original pure rule-update helpers, without importing its agent stack."""
    path = REFERENCES / 'LeapLabTHU_ExpeL/agent/expel.py'
    names = {'parse_rules', 'retrieve_rule_index', 'is_existing_rule', 'update_rules'}
    tree = ast.parse(path.read_text())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in body} != names:
        raise ValueError('Pinned ExpeL rule helpers missing')
    from typing import List, Tuple
    scope = {'re': re, 'List': List, 'Tuple': Tuple}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), scope)
    return scope['parse_rules'], scope['update_rules']


def expel_schedule(traces):
    by_task = defaultdict(list)
    for t in traces:
        by_task[t['task']].append(t)
    pairs, first_success = [], []
    for task, rows in sorted(by_task.items()):
        good = sorted([r for r in rows if r['success']], key=lambda r: (r['time'], r['id']))
        bad = sorted([r for r in rows if not r['success']], key=lambda r: (r['time'], r['id']))
        if good:
            first_success.append(good[0])
            for a in good:
                for b in bad:
                    pairs.append({'kind': 'compare', 'success': [a], 'failure': b})
    random.Random(42).shuffle(first_success)
    pairs.extend({'kind': 'all_success', 'success': first_success[i:i+8], 'failure': None}
                 for i in range(0, len(first_success), 8))
    return pairs


class DenseIndex:
    def __new__(cls, model, texts=None):
        if os.environ.get('R2G_EMBEDDING_PYTHON'):
            from t2_embedding_broker import RemoteIndex
            return RemoteIndex(model, texts)
        return super().__new__(cls)

    def __init__(self, model, texts=None):
        import faiss
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(2)
        self.model = SentenceTransformer(str(ROOT / 'embedding-models' / model), device='cpu',
                                         local_files_only=True, trust_remote_code=False)
        self.index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
        if texts:
            self.add(texts)

    def add(self, texts):
        self.index.add(self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).astype('float32'))

    def search(self, query, k):
        if not self.index.ntotal:
            return []
        _, ids = self.index.search(self.model.encode([query], normalize_embeddings=True).astype('float32'),
                                   min(k, self.index.ntotal))
        return [int(i) for i in ids[0] if i >= 0]

    def rebuild(self, texts):
        self.index.reset()
        if texts:
            self.add(texts)

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def note_text(note):
    return ('content:' + note['content'] + ' context:' + note['context'] + ' keywords: '
            + ', '.join(note['keywords']) + ' tags: ' + ', '.join(note['tags']))


def evolve_note(note, neighbors, decision):
    """Original strengthen/update_neighbor, with stable links replacing positional links."""
    if decision.get('should_evolve') is not True:
        return False
    for action in decision.get('actions', []):
        if action == 'strengthen':
            valid = {n['id'] for n in neighbors}
            for ident in decision.get('suggested_connections', []):
                if isinstance(ident, int) and 0 <= ident < len(neighbors):
                    ident = neighbors[ident]['id']
                if ident in valid:
                    note['links'].append(ident)
            note['tags'] = decision.get('tags_to_update', note['tags'])
        elif action == 'update_neighbor':
            contexts = decision.get('new_context_neighborhood', [])
            tags = decision.get('new_tags_neighborhood', [])
            for i, n in enumerate(neighbors[:len(tags)]):
                n['tags'] = tags[i]
                if i < len(contexts):
                    n['context'] = contexts[i]
    note['links'] = list(dict.fromkeys(note['links']))
    return True


def build_recipes(units):
    buckets = {}
    seen = set()
    for u in units:
        if u['unit_id'] in seen:
            continue
        seen.add(u['unit_id'])
        if not positive(u):
            continue
        action = u['action']
        if 'shared_label' in u:      # the action's effective after-values from its own row
            edits = dict(u['shared_label'].get('edits') or {})
        else:
            edits = action.get('config_delta') or {}
            if isinstance(edits, str):
                edits = json.loads(edits)
            edits = {k: ('<unset>' if v is None else str(v)) for k, v in edits.items()}
        if not edits or not action.get('from_stage'):
            continue
        sit = {k: situation(u).get(k) for k in RECIPE_KEYS}
        ident = fingerprint([sit, edits, action['from_stage']])
        entry = buckets.setdefault(ident, {'id': 'recipe_' + ident[:20], 'situation': sit,
                                          'config_edits': edits, 'rerun_from': action['from_stage'],
                                          'unit_ids': [], 'first_time': u['identity'].get('ts') or ''})
        entry['unit_ids'].append(u['unit_id'])
        entry['first_time'] = min(entry['first_time'], u['identity'].get('ts') or '')
    return sorted(buckets.values(), key=lambda x: (-len(x['unit_ids']), x['first_time'], x['id']))


def recipe_strategies(path, sit, cfg, exclude=(), limit=2):
    # Executor contract (failure-patterns "Memory candidate broke the executor contract"):
    # `recheck` comes from the recipe's own recorded check; an unmappable check stays None
    # so the shared gate reports an invalid candidate instead of executing a guess.
    knowledge = Path(__file__).resolve().parents[2] / 'r2g-skills/signoff-loop/knowledge'
    if str(knowledge) not in sys.path:
        sys.path.insert(0, str(knowledge))
    from candidate_contract import recheck_from_evidence
    data = json.loads(Path(path).read_text())
    out = []
    for recipe in data['recipes']:
        if recipe['id'] in exclude or any(recipe['situation'].get(k) != sit.get(k) for k in RECIPE_KEYS):
            continue
        if all(str(cfg.get(k)) == str(v) for k, v in recipe['config_edits'].items()):
            continue
        out.append({**recipe, 'source': 'recipe', 'rule_id': recipe['id'], 'auto_apply': True,
                    'recheck': recheck_from_evidence(recipe['situation'].get('check')),
                    'candidate_attempt_authorized': True, 'memory_trial': True,
                    'transformation_family': 'SOURCE_CONCRETE_RECIPE',
                    'description': 'Verified SOURCE recipe; successful evidence count %d' % len(recipe['unit_ids'])})
        if len(out) >= limit:
            break
    return out


class PromptMemory:
    def __init__(self, arm, root):
        self.arm, self.root = arm, Path(root)
        self.data = json.loads((self.root / (arm + '.json')).read_text())
        if self.data.get('status') != 'FROZEN':
            raise ValueError('Memory is not frozen: ' + arm)
        self.rows = self.data.get('trajectories', self.data.get('notes', []))
        self.by_id = {r['id']: r for r in self.rows}
        self.index = None
        if arm in ('RET', 'EXPEL', 'AMEM'):
            self.index = DenseIndex('all-MiniLM-L6-v2' if arm == 'AMEM' else 'all-mpnet-base-v2',
                                    [note_text(r) if arm == 'AMEM' else r['text'] for r in self.rows])

    def context(self, query, sit, auxiliary=None, max_bytes=5500, k=None):
        k = (10 if self.arm == 'AMEM' else 3) if k is None else k
        prefix, choices, ids = '', [], []
        if self.arm == 'AWM':
            key = stable([sit.get(k) for k in GROUP_KEYS])
            prefix = self.data['workflows'].get(key, '')
        else:
            if self.arm == 'EXPEL':
                prefix = 'Insights:\n' + '\n'.join(r[0] for r in self.data['rules']) + '\nExamples:\n'
            if self.arm == 'AMEM':
                if auxiliary is None:
                    raise ValueError('A-MEM requires its online query keyword call')
                keywords = auxiliary(query)
                if not keywords:return '',[]
                query = ' '.join(keywords)
                if not query.strip():return '',[]
            for i in self.index.search(query, k):
                row = self.rows[i]
                candidates = [row]
                if self.arm == 'AMEM':
                    candidates += [self.by_id[j] for j in row['links'] if j in self.by_id][:k]
                for r in candidates:
                    if r['id'] not in ids:
                        choices.append(note_text(r) if self.arm == 'AMEM' else r['text'])
                        ids.append(r['id'])
        if max_bytes is not None and len(prefix.encode()) > max_bytes:
            raise ValueError('Full insight/workflow set exceeds frozen context ceiling')
        kept, used = [], []
        for ident, text in zip(ids, choices):
            candidate = prefix + '\n\n'.join(kept + [text])
            if max_bytes is None or len(candidate.encode()) <= max_bytes:
                kept.append(text); used.append(ident)
        return prefix + '\n\n'.join(kept), used
