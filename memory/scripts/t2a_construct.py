#!/usr/bin/env python3
"""Build all five controls from SOURCE-v1, with resumable request receipts and a shared phase ledger."""
import argparse
import ast
import hashlib
from collections import defaultdict
import json
import os
from pathlib import Path
import resource
import re
import shutil
import sys
import textwrap
import time

import t2a_adapters as A
import t2a_qualify as Q

SOURCE = Q.EVAL / 'p1c-export/SOURCE-v1/source_export.jsonl'
ARMS = ('RET', 'RECIPE', 'EXPEL', 'AWM', 'AMEM')
CONSTRUCTION_INPUT_BYTES = {'C6': 48000, 'C7': 48000, 'C8': 48000, 'C9': 64000, 'C10': 96000}
INPUT_AMENDMENTS = {'C9': ('C8', 48000, 64000), 'C10': ('C9', 64000, 96000)}
DEFAULT_INPUT_BYTES = 12000  # formal repair and all pre-C6 construction snapshots


def write(path, data):
    temp = Path(str(path) + '.tmp')
    temp.write_text(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temp.replace(path)


def prepare(root):
    units = A.load_source(SOURCE)
    traces = A.trajectories(units)
    schedule = A.expel_schedule(traces)
    root.mkdir(parents=True, exist_ok=False)
    for name in ('t2a_construct.py', 't2a_adapters.py', 't2a_qualify.py', 'r2g_memory_eval.py'):
        dest = root / 'code/memory/scripts' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).parent / name, dest)
    rel = 'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    dest = root / 'code' / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Q.REPO / rel, dest)
    rel = 'r2g-skills/signoff-loop/knowledge/knob_policy.py'
    dest = root/'code'/rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Q.REPO/rel, dest)
    for name in ('LeapLabTHU_ExpeL/agent/expel.py', 'WujiangXu_A-mem/memory_layer.py',
                 'zorazrw_agent-workflow-memory/mind2web/prompt/instruction_abstract.txt'):
        dest = root/'code/upstream'/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(A.REFERENCES/name, dest)
    write(root / 'budget.json', {'budget_id': 't2a-construction-dev-A3',
          'caps': {'llm_requests': 5000}, 'combined_tokens_cap': 7000000, 'reserve': {}})
    ordered = sorted(units, key=lambda u: (u['identity'].get('ts') or '', u['unit_id']))
    order = [u['unit_id'] for u in ordered]
    write(root / 'registration.json', {'source_sha256': A.SOURCE_SHA, 'units': len(units),
          'created': time.time(), 'arms': ARMS, 'model': 'deepseek-flash', 'endpoint': 'https://api.deepseek.com/chat/completions',
          'input_bytes_ceiling': 12000, 'output_tokens_ceiling': 2000, 'transport_retries': 0,
          'temperatures': {'EXPEL': 0, 'AWM': 0, 'AMEM': 0.7}, 'expel_compare_calls': sum(s['kind']=='compare' for s in schedule),
          'expel_success_calls': sum(s['kind']=='all_success' for s in schedule), 'amem_order': order,
          'amem_order_sha256': A.fingerprint(order), 'expel_order_sha256': A.fingerprint(schedule),
          'code': {str(p.relative_to(root)): Q.digest(p) for p in (root/'code').rglob('*') if p.is_file()},
          'upstream_helpers_sha256': Q.digest(A.ROOT/'LeapLabTHU_ExpeL/agent/expel.py'),
          'upstream_amem_sha256': Q.digest(A.ROOT/'WujiangXu_A-mem/memory_layer.py'),
          'note': '923 ExpeL calls correct the draft estimate of 360; 4789 ExpeL+A-MEM calls fit A3. DEV uses the same allocation.'})
    base = {'status': 'FROZEN', 'source_sha256': A.SOURCE_SHA}
    write(root/'RET.json', {**base, 'trajectories': traces, 'model': 'all-mpnet-base-v2'})
    write(root/'RECIPE.json', {**base, 'recipes': A.build_recipes(units), 'match_keys': A.RECIPE_KEYS})
    print(json.dumps({'prepared': str(root), 'expel_calls': len(schedule), 'amem_calls': 2*len(units),
                      'recipes': len(A.build_recipes(units)), 'llm_requests_sent': 0}))


def quota_binding(root, authorization):
    """R2 changes only the phase token ceiling, never the ledger or other limits."""
    if authorization is None:raise ValueError('C8 quota authorization required')
    grant=json.loads(authorization.read_text())
    prior=root/'budget.json';ledger=root/'phase/resource_ledger.jsonl'
    budget=Path(grant.get('new_budget_file',''))
    if (grant.get('schema')!='t2a-construction-quota-amendment-v1' or grant.get('user_approved') is not True
        or grant.get('previous_token_cap')!=7000000 or grant.get('new_token_cap')!=17000000
        or grant.get('request_cap')!=5000 or grant.get('previous_budget_sha256')!=Q.digest(prior)
        or grant.get('previous_revision_sha256')!=Q.digest(root/'revisions/C7/registration.json')
        or grant.get('phase_ledger_path')!=str(ledger.resolve())
        or grant.get('prior_phase_ledger_sha256')!=Q.digest(ledger)):
        raise ValueError('Invalid C8 quota authorization or prior evidence')
    old=json.loads(prior.read_text());new=json.loads(budget.read_text())
    expected={**old,'combined_tokens_cap':17000000}
    if old.get('combined_tokens_cap')!=7000000 or new!=expected or new.get('caps')!={'llm_requests':5000}:
        raise ValueError('C8 quota amendment changes unapproved limits')
    return {'path':str(budget.resolve()),'sha256':Q.digest(budget),
            'authorization':str(authorization.resolve()),'authorization_sha256':Q.digest(authorization),
            'prior_phase_ledger_path':str(ledger.resolve()),'prior_phase_ledger_bytes':ledger.stat().st_size,
            'prior_phase_ledger_sha256':Q.digest(ledger),
            'previous_budget_sha256':Q.digest(prior),'previous_revision_sha256':grant['previous_revision_sha256']}


def input_binding(root, authorization, revision='C9'):
    """Raise offline input bytes only, reusing the exact C8 phase budget."""
    previous_revision,old_ceiling,new_ceiling=INPUT_AMENDMENTS[revision]
    if authorization is None:raise ValueError(revision+' input amendment authorization required')
    grant=json.loads(authorization.read_text());previous=root/'revisions'/previous_revision/'registration.json'
    prior=json.loads(previous.read_text());binding=prior.get('budget_amendment')
    ledger=root/'phase/resource_ledger.jsonl'
    if (not binding or grant.get('schema')!='t2a-construction-input-amendment-v1'
        or grant.get('user_approved') is not True or grant.get('source_sha256')!=A.SOURCE_SHA
        or grant.get('previous_revision_sha256')!=Q.digest(previous)
        or prior.get('input_bytes_ceiling')!=old_ceiling or grant.get('previous_input_bytes_ceiling')!=old_ceiling
        or grant.get('new_input_bytes_ceiling')!=new_ceiling or grant.get('formal_input_bytes_ceiling')!=12000
        or grant.get('output_tokens_ceiling')!=2000 or grant.get('transport_retries')!=0
        or grant.get('combined_tokens_cap')!=17000000 or grant.get('request_cap')!=5000
        or grant.get('budget_file')!=binding['path'] or grant.get('budget_sha256')!=binding['sha256']
        or Q.digest(Path(binding['path']))!=binding['sha256']
        or grant.get('phase_ledger_path')!=str(ledger.resolve())
        or grant.get('prior_phase_ledger_sha256')!=Q.digest(ledger)):
        raise ValueError('Invalid '+revision+' input amendment or prior evidence')
    budget=json.loads(Path(binding['path']).read_text())
    if budget.get('combined_tokens_cap')!=17000000 or budget.get('caps')!={'llm_requests':5000}:
        raise ValueError(revision+' input amendment cannot change the quota')
    receipt={'authorization':str(authorization.resolve()),'authorization_sha256':Q.digest(authorization),
             'previous_revision_path':str(previous.resolve()),
             'previous_revision_sha256':Q.digest(previous),'prior_phase_ledger_path':str(ledger.resolve()),
             'prior_phase_ledger_bytes':ledger.stat().st_size,'prior_phase_ledger_sha256':Q.digest(ledger)}
    return binding,receipt


def amend(root, revision, authorization=None):
    """Append a new immutable code registration; preserve original code and shared spending."""
    if not revision.isalnum():
        raise ValueError('Revision must be alphanumeric')
    budget_amendment=quota_binding(root,authorization) if revision=='C8' else None
    input_amendment=None
    if revision in INPUT_AMENDMENTS:budget_amendment,input_amendment=input_binding(root,authorization,revision)
    if revision == 'C7':
        if authorization is None:
            raise ValueError('C7 requires explicit UNKNOWN occupancy/retry authorization')
        grant=json.loads(authorization.read_text())
        if (grant.get('schema')!='r2g-unknown-occupancy-authorization-v1' or grant.get('user_approved') is not True
            or grant.get('retry',{}).get('attempts')!=1 or grant['retry'].get('revision')!='C7'):
            raise ValueError('Invalid C7 authorization')
    dest = root/'revisions'/revision
    dest.mkdir(parents=True, exist_ok=False)
    original = json.loads((root/'registration.json').read_text())
    for rel in original['code']:
        sub = Path(rel).relative_to('code')
        source = A.REFERENCES/sub.relative_to('upstream') if sub.parts[0]=='upstream' else Q.REPO/sub
        out=dest/'code'/sub;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,out)
    # Root progress/stop files are mutable views; preserve their pre-amendment
    # state before a resumed worker can replace them. Paid request receipts and
    # the shared ledger remain in their original locations.
    prior_state={}
    for arm in ARMS:
        for suffix in ('-progress.json','-stop.json'):
            prior=root/(arm+suffix)
            if prior.is_file():
                out=dest/'prior-state'/prior.name
                out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(prior,out)
                prior_state[str(out.relative_to(root))]=Q.digest(out)
    write(dest/'registration.json',{'revision':revision,'created':time.time(),
          'parent_registration_sha256':Q.digest(root/'registration.json'),
          'code':{str(p.relative_to(root)):Q.digest(p) for p in (dest/'code').rglob('*') if p.is_file()},
          'prior_state': prior_state,
          'input_bytes_ceiling': CONSTRUCTION_INPUT_BYTES.get(revision, DEFAULT_INPUT_BYTES),
          'ceiling_scope': 'offline_external_memory_construction_only',
          'formal_input_bytes_ceiling': DEFAULT_INPUT_BYTES,
          'output_tokens_ceiling': 2000, 'transport_retries': 0,
          'budget_amendment': budget_amendment,
          'input_amendment': input_amendment,
          'retry_authorization': ({'path':str(authorization.resolve()),'sha256':Q.digest(authorization)} if revision=='C7' else None),
          'note': ('C10: user authorizes raising the offline input limit and resuming; selected ceiling is 96000 serialized bytes. '
                   'Resume ExpeL after the unsent 64374-byte refusal, preserving all existing responses and cumulative costs. '
                   'Formal/DEV input remains 12000; exact C8 17000000-token/5000-request quota, UNKNOWN occupancy, '
                   'SOURCE, sampling, output settings and native parsing stay unchanged. No new retries or frozen-arm reruns.' if revision=='C10'
                   else 'C9: user raises offline construction input from 48000 to 64000 serialized bytes; '
                   'formal/DEV online input stays 12000, output ceilings/sampling/order/SOURCE unchanged. '
                   'Reuse the exact C8 17000000-token/5000-request budget and cumulative ledger, including UNKNOWN occupancy. '
                   'Reuse every cached response; no new retries and no rerun of frozen AMEM/AWM.' if revision=='C9'
                   else 'C8/R2: user raises construction+DEV total token ceiling from 7000000 to 17000000; '
                   'request cap remains 5000. Bind a new budget file without editing old budgets or resetting the shared ledger. '
                   'Preserve C7 UNKNOWN safety occupancy and consumed retry, C6 byte ceilings, cached responses, '
                   'SOURCE, order, native parse fallback and sampling. No additional transport retry.' if revision=='C8'
                   else 'C7: user-authorized original UNKNOWN request occupies its original 9415+1000 token bound, '
                   'separate from measured usage; permit exactly one signature/hash-bound explicit retry of AMEM evolution-0263. '
                   'Preserve original raw files, append-only ledgers, C6 inputs/order/sampling and unchanged total allocation. '
                   'Every other transport retry remains forbidden.' if revision=='C7'
                   else 'C6: user-authorized 48000 serialized bytes for offline construction only; reserve input by actual body bytes. '
                   'Formal/DEV repair stays at 12000 bytes. Preserve C4 parsing and the C5 A-MEM unit-5 cutover; '
                   'same sampling, SOURCE, order, shared ledger and 5000-request/7000000-actual-token allocation. '
                   'Reuse cached responses without resending; report actual construction and online tokens separately.' if revision=='C6'
                   else 'C5: from A-MEM unit 5, remove prompt indentation and use the native neighbor fields (no links). '
                   'Earlier cached inputs unchanged; input-ceiling refusal sent no request. Same quota and sampling.' if revision=='C5'
                   else 'C4 restores native parse-failure behavior: A-MEM defaults/no evolution and ExpeL complete operations only. '
                   'Reuse unchanged cached/raw responses after ledger reconciliation; no regeneration or allowance reset.' if revision=='C4'
                   else 'AWM valid workflow objects are rendered locally. Reuse unchanged cached responses; no regeneration, extra requests, or allowance reset.')})


def workflow_text(value):
    if isinstance(value, list) and not value:
        return ''  # explicit zero workflows; never invent text or retry
    if isinstance(value,str) and value.strip():
        return value
    if isinstance(value,(dict,list)) and value:
        # Preserve the exact model output, including complete abstract steps.
        return A.stable(value)
    raise ValueError('Missing induced workflow text')


def parse_answer(content, tolerate=False):
    try:
        answer=json.loads(content)
        if not isinstance(answer,dict):raise ValueError('Not an object')
        return answer
    except ValueError:
        if not tolerate:raise
        return {'_malformed_content':content}


def complete_operations(answer):
    text=answer.get('operations')
    if isinstance(text,str):
        return text  # unchanged native parser input; preserve the cached evolving prefix
    if not isinstance(text,str):
        raw=answer.get('_malformed_content','')
        # Recover only complete JSON-escaped characters, never invent missing
        # content. Native parse_rules then drops incomplete/no-period operations.
        m=re.search(r'"operations"\s*:\s*"((?:[^"\\]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4])*)',raw)
        text=json.loads('"'+m[1]+'"') if m else ''
    return text


class Calls:
    def __init__(self, root, arm, code=None, tolerate=False, input_bytes_ceiling=DEFAULT_INPUT_BYTES,
                 retry_authorization=None,budget_file=None):
        if input_bytes_ceiling not in {DEFAULT_INPUT_BYTES,*CONSTRUCTION_INPUT_BYTES.values()}:
            raise ValueError('Unregistered construction input ceiling')
        self.root, self.arm = root, arm
        self.project = root/'construction'/arm
        self.project.mkdir(parents=True, exist_ok=True)
        code = code or root/'code'
        self.H = Q.module('t2a_construction_harness', code/'memory/scripts/r2g_memory_eval.py')
        self.H.SKILL = code/'r2g-skills/signoff-loop'
        # A private module instance: never raise the shared formal repair ceiling.
        self.H.LLM_MAX_INPUT_BYTES = input_bytes_ceiling
        budget_file=budget_file or root/'budget.json'
        os.environ.update(R2G_BUDGET_FILE=str(budget_file), R2G_REQUIRE_LLM_BUDGET='1',
                          R2G_PHASE_LEDGER=str(root/'phase/resource_ledger.jsonl'),
                          R2G_PHASE_BUDGET_FILE=str(budget_file), R2G_BUDGET_COMPONENT='construction')
        registration=root/'registration.json'
        binding=json.loads(registration.read_text()).get('phase_binding') if registration.exists() else None
        if binding:
            if Q.digest(Path(binding['budget_file']))!=binding['budget_sha256']:
                raise RuntimeError('Construction phase budget changed')
            os.environ.update(R2G_PHASE_LEDGER=binding['ledger_path'],
                              R2G_PHASE_BUDGET_FILE=binding['budget_file'])
        self.key = self.H.llm_key('/data1/zhangdy/.llm_env')
        self.count = 0
        self.tolerate=tolerate
        self.retry_authorization=retry_authorization

    def authorized_retry_prefix(self, ident, signature, messages, temperature, max_tokens):
        """One explicit retry; never overwrite raw evidence or retry a failed retry."""
        authorization=getattr(self,'retry_authorization',None)
        if not authorization:return None
        path=Path(authorization['path'])
        if Q.digest(path)!=authorization['sha256']:raise RuntimeError('Retry authorization changed')
        grant=json.loads(path.read_text());retry=grant.get('retry',{})
        if retry.get('arm')!=self.arm or retry.get('ident')!=ident:return None
        prefix=self.project/ident
        req=Path(str(prefix)+'.try0.request.json');response=Path(str(prefix)+'.try0.response.json')
        expected={'model':self.H.LLM_API_MODEL,'temperature':temperature,'max_tokens':max_tokens,'stream':False,
                  'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'messages':messages}
        if (grant.get('user_approved') is not True or retry.get('attempts')!=1 or retry.get('revision')!='C7'
            or retry.get('signature')!=signature or Q.digest(req)!=retry.get('request_sha256')
            or Q.digest(response)!=retry.get('response_sha256') or json.loads(req.read_text())!=expected
            or retry.get('model')!=self.H.LLM_API_MODEL or retry.get('endpoint')!=self.H.LLM_ENDPOINT):
            raise RuntimeError('Authorized retry request changed')
        targets=grant.get('targets',[])
        local=next((t for t in targets if t['ledger_path']==str((self.project/'reports/resource_ledger.jsonl').resolve())),None)
        if not local or len(targets)!=2:raise RuntimeError('Retry requires local and shared UNKNOWN occupancies')
        original=None
        for target in targets:
            rows=[json.loads(l) for l in Path(target['ledger_path']).read_text().splitlines() if l]
            matches=[e for e in rows if e.get('type')=='budget_unknown_occupancy'
                     and e.get('hold_id')==target['hold_id'] and e.get('authorization')==str(path.resolve())
                     and e.get('authorization_sha256')==authorization['sha256']
                     and e.get('upper_bound')==target['upper_bound']]
            if len(matches)!=1:raise RuntimeError('Authorized UNKNOWN occupancy not applied')
            if target==local:
                calls=[e for e in rows if e.get('type')=='llm_request' and e.get('hold_id')==local['hold_id']]
                if len(calls)==1:original=calls[0]
        if not original or original.get('status')!=-1 or original.get('usage_reported')!='UNKNOWN' or original.get('model_mismatch'):
            raise RuntimeError('Explicit retry is not the registered transport failure')
        shared=next(t for t in targets if t!=local)
        if original.get('phase_hold_id')!=shared['hold_id']:raise RuntimeError('Retry phase lineage mismatch')
        retry_prefix=Path(str(prefix)+'.authorized-retry-C7-1')
        if list(self.project.glob(retry_prefix.name+'.try*')):raise RuntimeError('Explicit retry already claimed')
        claim=Path(str(retry_prefix)+'.claim.json')
        lineage={**retry,'authorization':str(path.resolve()),'authorization_sha256':authorization['sha256'],
                 'retry_prefix':str(retry_prefix),'original_hold_id':local['hold_id'],'original_phase_hold_id':shared['hold_id']}
        try:
            with claim.open('x') as fh:
                fh.write(A.stable(lineage)+'\n');fh.flush();os.fsync(fh.fileno())
        except FileExistsError:raise RuntimeError('Explicit retry already claimed')
        rc,_=self.H._ledger(self.project,'event','--type','explicit_model_retry','--json',A.stable(lineage))
        if rc:raise RuntimeError('Explicit retry claim accounting failed')
        return retry_prefix,lineage

    def ask(self, ident, prompt, temperature, max_tokens=800):
        messages = [{'role': 'system', 'content': 'Respond with one JSON object only.'},
                    {'role': 'user', 'content': prompt}]
        prefix = self.project/ident
        cached = Path(str(prefix)+'.answer.json')
        signature = A.fingerprint([messages, temperature, max_tokens])
        if cached.exists():
            data = json.loads(cached.read_text())
            if data['signature'] != signature:
                raise RuntimeError('Checkpoint request changed: '+ident)
            return data['answer']
        previous=Path(str(prefix)+'.try0.response.json')
        authorized=self.authorized_retry_prefix(ident,signature,messages,temperature,max_tokens) if previous.exists() else None
        if previous.exists() and not authorized:
            if not getattr(self,'tolerate',False):raise RuntimeError('Unreconciled previous response: '+ident)
            raw=json.loads(previous.read_text())
            body=Path(str(prefix)+'.try0.request.json').read_bytes()
            expected={'model':self.H.LLM_API_MODEL,'temperature':temperature,'max_tokens':max_tokens,'stream':False,
                      'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'messages':messages}
            if json.loads(body)!=expected:raise RuntimeError('Raw response request changed: '+ident)
            events=[json.loads(l) for l in (self.project/'reports/resource_ledger.jsonl').read_text().splitlines()]
            matches=[e for e in events if e.get('type')=='llm_request' and e.get('request_id')==raw.get('id')
                     and e.get('status')==200 and not e.get('model_mismatch')]
            settled={e['hold_id'] for e in events if e.get('type')=='budget_settle'}
            if (len(matches)!=1 or matches[0]['hold_id'] not in settled
                    or raw.get('model') not in self.H.LLM_ALLOWED_REPORTED_MODELS):
                raise RuntimeError('Unreconciled metering for raw response: '+ident)
            answer=parse_answer(raw['choices'][0]['message']['content'],True)
            write(cached,{'signature':signature,'answer':answer,'usage':raw['usage'],
                          'wall_s':matches[0]['latency_s'],'role':'offline_construction','reconciled_raw':True})
            return answer
        t0 = time.time()
        out_prefix=authorized[0] if authorized else prefix
        content, usage = self.H.llm_call(self.key, messages, out_prefix, ledger=self.project, role='auxiliary',
                                       sampling_override={'temperature': temperature, 'max_tokens': max_tokens})
        if content is None or usage.get('accounting_error') or not isinstance(usage.get('total_tokens'), (int, float)):
            raise RuntimeError('Construction request stopped: '+str(usage))
        answer = parse_answer(content,getattr(self,'tolerate',False))
        write(cached, {'signature': signature, 'answer': answer, 'usage': usage,
                       'wall_s': time.time()-t0, 'role': 'offline_construction',
                       **({'explicit_retry':authorized[1]} if authorized else {})})
        self.count += 1
        return answer


def amem_prompts():
    tree = ast.parse((A.REFERENCES/'WujiangXu_A-mem/memory_layer.py').read_text())
    attr, evolution = None, None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == 'evolution_system_prompt':
                evolution = ast.literal_eval(node.value)
            if isinstance(target, ast.Name) and target.id == 'prompt' and isinstance(node.value, ast.BinOp):
                if isinstance(node.value.left, ast.Constant) and 'Generate a structured analysis' in str(node.value.left.value):
                    attr = node.value.left.value
    if not attr or not evolution:
        raise RuntimeError('Pinned A-MEM prompts not found')
    return attr, evolution


def construction_source(registration):
    """New NB folds use their hash-bound package; legacy registrations stay unchanged."""
    if 'shared_package' not in registration:
        return A.load_source(SOURCE), A.SOURCE_SHA
    package = Path(registration['shared_package'])
    if Q.digest(package/'manifest.json') != registration['shared_manifest_sha256']:
        raise ValueError('construction shared manifest changed')
    units = A.load_shared(package)
    if Q.digest(package/'shared_units.jsonl') != registration['shared_units_sha256']:
        raise ValueError('construction shared units changed')
    return units, registration['shared_units_sha256']


def construct(root, arm, revision=None):
    import fcntl
    lock = (root/(arm+'.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (root/(arm+'.json')).exists():
        raise RuntimeError('Representation already frozen')
    registration = json.loads((root/'registration.json').read_text())
    for rel, digest in registration['code'].items():
        if Q.digest(root/rel) != digest:
            raise RuntimeError('Construction code changed: '+rel)
    code=root/'code'
    input_bytes_ceiling=registration['input_bytes_ceiling']
    retry_authorization=None
    budget_file=root/'budget.json'
    if revision:
        addition=json.loads((root/'revisions'/revision/'registration.json').read_text())
        if addition['parent_registration_sha256']!=Q.digest(root/'registration.json'):
            raise RuntimeError('Revision parent changed')
        for rel,digest in addition['code'].items():
            if Q.digest(root/rel)!=digest:
                raise RuntimeError('Revision code changed: '+rel)
        code=root/'revisions'/revision/'code'
        input_bytes_ceiling=addition.get('input_bytes_ceiling', registration['input_bytes_ceiling'])
        if input_bytes_ceiling != CONSTRUCTION_INPUT_BYTES.get(revision, DEFAULT_INPUT_BYTES):
            raise RuntimeError('Revision input ceiling changed')
        retry_authorization=addition.get('retry_authorization')
        if retry_authorization and Q.digest(Path(retry_authorization['path']))!=retry_authorization['sha256']:
            raise RuntimeError('Revision retry authorization changed')
        binding=addition.get('budget_amendment')
        if revision in ('C8',*INPUT_AMENDMENTS) and not binding:raise RuntimeError('Quota binding missing')
        if binding:
            budget_file=Path(binding['path'])
            if Q.digest(budget_file)!=binding['sha256']:raise RuntimeError('Revision budget changed')
            if Q.digest(Path(binding['authorization']))!=binding['authorization_sha256']:
                raise RuntimeError('Quota authorization changed')
            prefix=Path(binding['prior_phase_ledger_path']).read_bytes()[:binding['prior_phase_ledger_bytes']]
            if hashlib.sha256(prefix).hexdigest()!=binding['prior_phase_ledger_sha256']:
                raise RuntimeError('Prior shared ledger changed')
        inputs=addition.get('input_amendment')
        if revision in INPUT_AMENDMENTS and not inputs:raise RuntimeError(revision+' input authorization missing')
        if inputs:
            if Q.digest(Path(inputs['authorization']))!=inputs['authorization_sha256']:
                raise RuntimeError('Input authorization changed')
            if inputs.get('previous_revision_path') and Q.digest(Path(inputs['previous_revision_path']))!=inputs['previous_revision_sha256']:
                raise RuntimeError('Prior input revision changed')
            prefix=Path(inputs['prior_phase_ledger_path']).read_bytes()[:inputs['prior_phase_ledger_bytes']]
            if hashlib.sha256(prefix).hexdigest()!=inputs['prior_phase_ledger_sha256']:
                raise RuntimeError('Prior input-amendment ledger changed')
    units, source_sha = construction_source(registration)
    traces = A.trajectories(units)
    t0, cpu = time.time(), time.process_time()
    data = {'source_sha256': source_sha, 'units_sha256': source_sha,
            'status': 'FROZEN', 'arm': arm,'code_revision':revision or 'original',
            'construction_input_bytes_ceiling': input_bytes_ceiling,
            'construction_budget_sha256':Q.digest(budget_file) if budget_file.is_file() else None}
    def progress(done, total):
        write(root/(arm+'-progress.json'), {'status': 'BUILDING', 'completed': done, 'total': total,
                                          'updated': time.time(), 'new_requests_this_process': calls.count})
        print(json.dumps({'arm': arm, 'completed': done, 'total': total}), flush=True)
    try:
        calls = Calls(root, arm, code, tolerate=registration.get('native_parse_fallback') is True or revision in ('C4','C5','C6','C7','C8','C9','C10'),
                      input_bytes_ceiling=input_bytes_ceiling,retry_authorization=retry_authorization,budget_file=budget_file)
        malformed=[]
        if arm == 'EXPEL':
            parse, update = A.expel_operations()
            rules = []
            schedule = A.expel_schedule(traces)
            for i, job in enumerate(schedule):
                prompt = ('Compare the verified successful and unsuccessful physical-design repair trajectories, or generalize the all-success chunk. '
                          'Update reusable insights from the evidence, without claiming universal effectiveness. '
                          'Use ADD: text., EDIT n: text., REMOVE n: existing text., AGREE n: existing text. '
                          'Return JSON {"operations": "one operation per line"}. Existing rules:\n'
                          + '\n'.join('%d. %s' % (j+1,r[0]) for j,r in enumerate(rules))
                          + '\nSuccessful trajectories:\n' + '\n\n'.join(x['text'] for x in job['success']))
                if job['failure']:
                    prompt += '\nUnsuccessful trajectory:\n'+job['failure']['text']
                prompt += ('\nThe rule list is full: consolidate or remove low-value rules.' if len(rules)>=10
                           else '\nPrefer a concise set of at most ten useful insights.')
                answer = calls.ask('critique-%04d'%i, prompt, 0)
                if '_malformed_content' in answer:malformed.append('critique-%04d'%i)
                operations = parse(complete_operations(answer))
                rules = update(rules, operations, list_full=len(rules)>=10)
                progress(i+1, len(schedule))
            by_task = defaultdict(list)
            for trace in traces:
                if trace['success']:
                    by_task[trace['task']].append(trace)
            data.update(rules=rules, trajectories=[min(rows,key=lambda x:(len(x['text']),x['id']))
                                                  for _,rows in sorted(by_task.items())],
                        schedule_sha256=A.fingerprint(schedule), original_rule_helpers=True)
        elif arm == 'AWM':
            groups = defaultdict(dict)
            for trace in traces:
                if trace['success']:
                    key = A.stable([trace['situation'].get(k) for k in A.GROUP_KEYS])
                    groups[key].setdefault(A.fingerprint(trace['situation']), trace)
            original = (A.REFERENCES/'zorazrw_agent-workflow-memory/mind2web/prompt/instruction_abstract.txt').read_text()
            instruction = original.replace('web nagivation', 'physical-design repair').replace('button strings', 'design names and knob values')
            workflows, empty_workflows = {}, []
            for i,(group,examples) in enumerate(sorted(groups.items())):
                chunks, buf = [], []
                for trace in examples.values():
                    if len(('\n\n'.join(buf+[trace['text']])).encode())>8000 and buf:
                        chunks.append(buf); buf=[]
                    buf.append(trace['text'])
                if buf: chunks.append(buf)
                results=[]
                for j,chunk in enumerate(chunks):
                    prompt = instruction + '\nReturn JSON {"workflows": "name, Given that description, and at least two abstract steps per workflow"}. '
                    prompt += 'Be concise; retain invariant knob names and express design-specific values as {variables}.\n\n'+ '\n\n'.join(chunk)
                    answer = calls.ask('workflow-%02d-%02d'%(i,j),prompt,0,1200)
                    text = workflow_text(answer.get('workflows'))
                    if text:
                        results.append(text)
                    else:
                        empty_workflows.append({'call_id': 'workflow-%02d-%02d' % (i,j), 'group': group})
                if len(results)>1:
                    answer=calls.ask('workflow-merge-%02d'%i,instruction+'\nMerge these workflows, removing overlap without adding evidence. Return JSON {"workflows": "complete concise workflow set"}.\n'+'\n'.join(results),0,1200)
                    results=[workflow_text(answer.get('workflows'))]
                    if not results[0]:
                        empty_workflows.append({'call_id': 'workflow-merge-%02d' % i, 'group': group})
                workflows[group]='\n'.join(results)
                progress(i+1,len(groups))
            data.update(workflows=workflows, empty_workflow_results=empty_workflows,
                        empty_workflow_policy='EXPLICIT_EMPTY_LIST_IS_ZERO_WORKFLOWS_NO_RETRY',
                        construction_code_sha256=Q.digest(Path(__file__)), input_mode='official WebArena template deduplication',
                        domain_instruction=instruction)
        elif arm == 'AMEM':
            attr, evolution = amem_prompts()
            ordered = sorted(units, key=lambda u:(u['identity'].get('ts') or '',u['unit_id']))
            if [u['unit_id'] for u in ordered] != registration['amem_order']:
                raise RuntimeError('A-MEM insertion order changed')
            index = A.DenseIndex('all-MiniLM-L6-v2')
            notes=[]; evolved=0
            for i,u in enumerate(ordered):
                a=calls.ask('attribute-%04d'%i,attr+u['text_view'],0.7)
                if '_malformed_content' in a:
                    malformed.append('attribute-%04d'%i);a={'context':'General','keywords':[],'tags':[]}
                if isinstance(a.get('context'),list):a['context']=' '.join(a['context'])
                if not isinstance(a.get('context'),str) or not all(isinstance(a.get(k),list) and
                     all(isinstance(v,str) for v in a[k]) for k in ('keywords','tags')):
                    raise RuntimeError('Invalid A-MEM attributes')
                note={'id':u['unit_id'],'content':u['text_view'], 'context':a['context'],
                      'keywords':a['keywords'],'tags':a['tags'],'links':[], 'timestamp':u['identity'].get('ts')}
                neighbors=[notes[j] for j in index.search(note['content'],5)]
                # Stable IDs are exposed instead of transient retrieval positions.
                template=evolution
                neighbor_view=A.stable(neighbors)
                if (registration.get('native_parse_fallback') is True or revision in ('C5','C6','C7','C8','C9','C10')) and i>=5:
                    template=textwrap.dedent(evolution).strip()
                    # The native find_related_memories exposes these attributes,
                    # not the neighbor's own links; keep every retrieved neighbor.
                    neighbor_view='\n'.join('memory id: '+n['id']+'\t time: '+str(n['timestamp'])+
                         '\t content: '+n['content']+'\t context: '+n['context']+
                         '\t keywords: '+A.stable(n['keywords'])+'\t tags: '+A.stable(n['tags']) for n in neighbors)
                prompt=template.format(context=note['context'],content=note['content'], keywords=note['keywords'],
                     nearest_neighbors_memories=neighbor_view,neighbor_number=len(neighbors))
                decision=calls.ask('evolution-%04d'%i,prompt,0.7,1000)
                if '_malformed_content' in decision:
                    malformed.append('evolution-%04d'%i);decision={'should_evolve':False}
                if not isinstance(decision.get('should_evolve'),bool):
                    raise RuntimeError('Missing A-MEM evolution decision')
                for field in ('tags_to_update',):
                    if field in decision and (not isinstance(decision[field], list) or
                       not all(isinstance(v,str) for v in decision[field])):
                        raise RuntimeError('Invalid A-MEM '+field)
                if not all(isinstance(v,str) for v in decision.get('new_context_neighborhood',[])):
                    raise RuntimeError('Invalid A-MEM neighbor contexts')
                if not all(isinstance(v,list) and all(isinstance(t,str) for t in v)
                           for v in decision.get('new_tags_neighborhood',[])):
                    raise RuntimeError('Invalid A-MEM neighbor tags')
                evolved+=A.evolve_note(note,neighbors,decision)
                notes.append(note);index.add([A.note_text(note)])
                if decision['should_evolve'] and evolved%100==0:
                    index.rebuild([A.note_text(n) for n in notes])
                progress(i+1,len(ordered))
            data.update(notes=notes, evolved_adds=evolved, insertion_order_sha256=registration['amem_order_sha256'],
                        model='all-MiniLM-L6-v2', consolidation_after_evolving_adds=100)
        else:
            raise ValueError('Construction is required only for EXPEL/AWM/AMEM')
        data.update(wall_s=time.time()-t0,cpu_s=time.process_time()-cpu,
                    peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,parse_failures=malformed)
        write(root/(arm+'.json'),data)
        write(root/(arm+'-progress.json'),{'status':'FROZEN','representation_sha256':Q.digest(root/(arm+'.json')),
                                         'updated':time.time()})
    except BaseException as exc:
        write(root/(arm+'-stop.json'),{'status':'STOPPED','reason':str(exc),'updated':time.time(),
                                     'wall_s':time.time()-t0,'cpu_s':time.process_time()-cpu})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','build','amend'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--arm',choices=['EXPEL','AWM','AMEM'])
    parser.add_argument('--revision')
    parser.add_argument('--authorization',type=Path)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.root.resolve())
    elif args.action=='amend' and args.revision:amend(args.root.resolve(),args.revision,args.authorization)
    elif args.arm:construct(args.root.resolve(),args.arm,args.revision)
    else:parser.error('--arm required')
