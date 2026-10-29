"""Pinned ReMe/ReasoningBank procedural-memory domain adaptations.

No upstream SDK imports: generation uses the audited DeepSeek client; local
embeddings use the pinned CPU broker. These are named EDA adaptations, not
claims of exact paper reproduction. TEST memories are immutable.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import time

import t2a_adapters as A
import t2a_construct as C

REME_SHA = '554eec1cb94821212c8b81d5499c9e7f42de5c16'
RB_SHA = 'ed80611788292ea739f1effd31f16c53823b8a0d'
MODEL = 'all-mpnet-base-v2'
CONFIG = {
    'REME': {'upstream_commit': REME_SHA, 'upstream_tag': 'v0.2.0.6',
        'setting': 'procedural fixed memory; upstream default retrieval configuration',
        'success_threshold': 1.0, 'soft_comparison': True, 'hard_comparison': False,
        'validation_threshold': .5, 'dedup_cosine_threshold': .5,
        'top_k': 5, 'llm_query': False, 'llm_rerank': False, 'llm_rewrite': False,
        'embedding': MODEL, 'online_update': False},
    'REASONINGBANK': {'upstream_commit': RB_SHA, 'setting': 'SWE memory-only EDA adaptation',
        'max_items_per_trajectory': 3, 'retrieved_trajectories': 1, 'MaTTS': False,
        'embedding': MODEL, 'online_update': False}}


def yaml_prompt(path, key):
    """Read the pinned plain YAML literal, without optional YAML dependencies."""
    text = Path(path).read_text()
    match = re.search(r'^' + re.escape(key) + r':\s*\|\s*\n((?:[ \t]+[^\n]*(?:\n|$)|\n)*)', text, re.M)
    if not match:
        raise ValueError('Pinned prompt not found: ' + key)
    import textwrap
    return textwrap.dedent(match[1]).strip()


def rb_prompts(path):
    found = {}
    for n in ast.parse(Path(path).read_text()).body:
        if isinstance(n, ast.Assign):
            for name in n.targets:
                if isinstance(name, ast.Name) and name.id in ('SUCCESSFUL_SI', 'FAILED_SI'):
                    found[name.id] = ast.literal_eval(n.value)
    if set(found) != {'SUCCESSFUL_SI', 'FAILED_SI'}:
        raise ValueError('Pinned ReasoningBank prompts missing')
    return found


def native_items(answer, maximum=3):
    """Transport wraps upstream JSON arrays; empty is valid, malformed is not retried."""
    values = answer.get('memories')
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError('Malformed or over-count memory extraction')
    for item in values:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ('when_to_use', 'experience')):
            raise ValueError('Malformed ReMe memory')
    return values


def parse_rb(markdown):
    if not isinstance(markdown, str):
        raise ValueError('ReasoningBank markdown missing')
    chunks = re.split(r'^# Memory Item\s+\d+\s*$', markdown.strip(), flags=re.M)
    if len(chunks) < 2 or chunks[0].strip() or len(chunks) > 4:
        raise ValueError('Malformed ReasoningBank item boundaries')
    out = []
    for chunk in chunks[1:]:
        m = re.fullmatch(r'\s*## Title:?\s+(.+?)\s*\n## Description:?\s+(.+?)\s*\n## Content:?\s+(.+?)\s*', chunk, re.S)
        if not m or any(not x.strip() for x in m.groups()):
            raise ValueError('Malformed ReasoningBank item fields')
        out.append(dict(zip(('title', 'description', 'content'), (x.strip() for x in m.groups()))))
    return out


def cosine_dedup(items, vectors, threshold=.5):
    if len(items) != len(vectors):
        raise ValueError('Embedding alignment mismatch')
    kept, discarded, seen = [], [], []
    for item, v in zip(items, vectors):
        norm = sum(x*x for x in v)**.5
        if not norm:
            raise ValueError('Zero embedding, cannot deduplicate')
        v = [x/norm for x in v]
        if any(sum(x*y for x,y in zip(v,w)) > threshold for w in seen):
            discarded.append(item['id'])
        else:
            kept.append(item);seen.append(v)
    return kept, discarded


def build(root, arm):
    import fcntl
    root = Path(root)
    lock = (root/(arm+'.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if arm not in CONFIG or (root/(arm+'.json')).exists():
        raise ValueError('Unknown arm or already built')
    reg = json.loads((root/'registration.json').read_text())
    for rel, expected in reg['code'].items():
        if C.Q.digest(root/rel) != expected:
            raise ValueError('Construction code changed: '+rel)
    units, source_hash = C.construction_source(reg)
    traces = A.trajectories(units)
    start, cpu = time.time(), time.process_time()
    calls = C.Calls(root, arm, code=root/'code', input_bytes_ceiling=96000)
    upstream = root/'code/upstream'
    result = {'status':'FROZEN', 'units_sha256':source_hash, 'source_sha256':source_hash,
              'adapter':'agent9-native-algorithm-port-v1', 'configuration':CONFIG[arm],
              'deviations':['EDA task/action/strict-outcome schema instead of original domain',
                'DeepSeek through shared accounting instead of upstream provider SDK',
                'local pinned all-mpnet-base-v2 instead of upstream remote embedding',
                'JSON transport wrapper; strict malformed-output stop without retry'],
              'evidence_notice':'Generated experience is advice, not verified causal authority.'}
    if arm == 'REASONINGBANK':
        prompts = rb_prompts(upstream/'ReasoningBank/third_party/src/minisweagent/memory/instruction.py')
        records = []
        for i, t in enumerate(traces):
            prompt = prompts['SUCCESSFUL_SI' if t['success'] else 'FAILED_SI']
            ident, text = 'extract-%03d'%i, (prompt +
                '\nDomain: R2G physical design. Preserve uncertainty and rollback labels.\n' + t['text'] +
                '\nTransport only: return {"markdown": "the requested Markdown items"}.')
            answer = calls.ask(ident, text, 0)
            try:
                items = parse_rb(answer.get('markdown'))
            except ValueError as exc:
                # User rule 2026-10-23: one explicit re-send of the same prompt; strict parser unchanged.
                retried = calls.malformed_retry(ident, text, 0, 800, str(exc)) if hasattr(calls, 'malformed_retry') else None
                if retried is None:
                    raise
                items = parse_rb(retried.get('markdown'))
            records.append({'id':t['id'], 'task':t['task'], 'success':t['success'],
                            'query':t['task']+' '+A.stable(t['situation']),
                            'items':items, 'unit_ids':t['units']})
        result['trajectories'] = records
    else:
        base = upstream/'ReMe/reme_ai/summary/task'
        candidates=[]
        def extract(ident, prompt, source_ids, maximum=3):
            answer=calls.ask(ident,prompt+'\nDomain: R2G/ORFS physical repair. Preserve all uncertainty labels.'+
                            '\nTransport only: wrap the requested array in {"memories": [...]}.',0)
            for item in native_items(answer,maximum):
                candidates.append({**item,'id':A.fingerprint([ident,item]),'source_trajectory_ids':source_ids})
        for i,t in enumerate(traces):
            kind='success' if t['success'] else 'failure'
            prompt=yaml_prompt(base/(kind+'_extraction_prompt.yaml'),kind+'_step_task_memory_prompt')
            extract('extract-%03d'%i,prompt.format(query=t['task'],step_sequence=t['text'],
                    context=A.stable(t['situation']),outcome='successful' if t['success'] else 'failed'),[t['id']])
        # Native soft comparison: highest versus lowest score, only when unequal.
        ordered=sorted(traces,key=lambda t:int(t['success']),reverse=True)
        if len(ordered)>1 and ordered[0]['success'] != ordered[-1]['success']:
            high,low=ordered[0],ordered[-1]
            prompt=yaml_prompt(base/'comparative_extraction_prompt.yaml','soft_comparative_step_task_memory_prompt')
            extract('soft-comparison',prompt.format(higher_steps=high['text'],lower_steps=low['text'],
                    higher_score='1.00',lower_score='0.00'),[high['id'],low['id']],2)
        validated=[];validations=[]
        template=yaml_prompt(base/'memory_validation_prompt.yaml','task_memory_validation_prompt')
        for i,m in enumerate(candidates):
            a=calls.ask('validate-%03d'%i,template.format(condition=m['when_to_use'],task_memory_content=m['experience']),0)
            score=a.get('score')
            if type(a.get('is_valid')) is not bool or isinstance(score,bool) or not isinstance(score,(float,int)) or not 0<=score<=1:
                raise ValueError('Malformed ReMe validation; no permissive validity default')
            validations.append({'id':m['id'],'assessment':a})
            if a['is_valid'] and score>=.5:validated.append(m)
        index=A.DenseIndex(MODEL)
        try:
            vectors=index.encode([m['when_to_use']+' '+m['experience'] for m in validated]) if validated else []
        finally:
            if hasattr(index,'close'):index.close()
        kept,dropped=cosine_dedup(validated,vectors)
        result.update(memories=kept,validation=validations,deduplicated_ids=dropped,
                      extraction_candidates=candidates)
    result.update(construction_wall_s=time.time()-start,construction_cpu_s=time.process_time()-cpu,
                  model_requests=calls.count,transport_retries=0)
    C.write(root/(arm+'.json'),result)


class NativeMemory:
    def __init__(self, arm, controls):
        self.arm=arm
        self.data=json.loads((Path(controls)/(arm+'.json')).read_text())
        if self.data.get('status')!='FROZEN' or self.data.get('configuration')!=CONFIG[arm]:
            raise ValueError('Unregistered new-method configuration')
        self.rows=self.data.get('memories',self.data.get('trajectories',[]))
        texts=[r['when_to_use'] if arm=='REME' else r['query'] for r in self.rows]
        self.index=A.DenseIndex(MODEL,texts)

    def context(self, query):
        count=5 if self.arm=='REME' else 1
        selected=[self.rows[i] for i in self.index.search(query,count)]
        # ReMe's pinned default: explicit query, scenario recall, no model rerank/rewrite.
        # ReasoningBank SWE: retrieve one trajectory and present ALL of its items.
        items=[{'when_to_use':r['when_to_use'],'experience':r['experience']} for r in selected] if self.arm=='REME' else [m for r in selected for m in r['items']]
        return ('Historical advisory memory (not a verified current-task action):\n'+A.stable(items)
                if items else ''),[r['id'] for r in selected]


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--arm',choices=tuple(CONFIG),required=True);a=ap.parse_args();build(a.root,a.arm)
