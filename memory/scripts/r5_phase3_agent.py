"""R5 Phase 3c: agent consumer of TEHM Memory on discriminating tasks, sampled, with verified multi-role write-back
(card memory/evaluation/research_r5_phase3c_discriminating_agent_contract_20260929.md; Phase 3 r2 used d3b1304).

usage:
  r5_phase3_agent.py dry-run OUT_DIR                   build every request and render; no calls
  r5_phase3_agent.py run --gate G --credentials F      registered run into ROOT
  r5_phase3_agent.py audit                             separate-process cold audit of ROOT
Routing uses the frozen v3-M0 worktree (0128377); the source binder is never used to render Memory.
"""
import difflib
import functools
import hashlib
import json
import math
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import r5_i2c_v3_consumer_checks as base  # noqa: E402  (imports frozen tehm from the v3-M0 worktree)
from contracts import MemoryQuery  # noqa: E402
from tehm.assets import r5_train_evidence_i2c_v3 as evidence  # noqa: E402
from tehm.assets import r5_train_raw_i2c_v3 as raw  # noqa: E402
from tehm.evaluation import research_r5_train_m0_i2c_v3 as m0  # noqa: E402
from tehm.retrieval.memory_router import route_memory  # noqa: E402
from tehm.rtl.i2c_nack_action_v2 import decode_closure  # noqa: E402
from tehm.rtl.i2c_nack_action_v3 import payload_from_source_i2c_v3  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402

PILOT = base.PILOT
ROOT = PILOT / 'agent/p3c-agent-r1'
CTRL = PILOT / 'controlled/i2c-nack-pop-v1'
SPEC = CTRL / 'spec/spec-i2c-nack-master-v1.md'
SPEC_SHA = 'a9ceb440a31228e09c9accba202ad8c6460514c1b961db8ed443d108df1fbeb8'
ORACLE_TB_SHA = 'ee67a1b38125416876f042d19c22f16558373590f976eb32833f6ea724989690'
ORACLE_RUN_SHA = '71eed56c20aa67342c3995e3c6016c51f6351b1c5126fe88e9fad82c285e2f2d'
CLEAN = {7: (CTRL / 'generate/live-main-r1/design-07.v', 'daa7836a02d384c3865cba5dee9d9fb0abbd5f057186327737e48a1b3a124b9d'),
         25: (CTRL / 'generate/live-tf-r1/design-25.v', 'fd606f883df5c431ed5d9e73f0bb9100f03611e5c47d1bec6bb554ef73872b2d')}
COPY = {7: (CTRL / 'admit/run-r2/d07/fault.v', '612456bf5bdf2e78d080541e6593b3b7f84537f5aee88538852e37f9114ab87e'),
        25: (CTRL / 'admit/run-tf-r1/admission/d25/fault.v', 'd3242b2efe38e7e96e7b23110520a41df94a9c23450f4ffc891ce3ddcf2402b4')}
EVT_SITE = re.compile(r"(?<![\w.])next_nack\s*=\s*1'b1\s*;")
LINE_FAULTS = {  # design -> kind -> (line, old statement, new statement)
    7: {'holdloss': (104, 'next_nack = nack;', "next_nack = 1'b0;"),
        'clearloss': (126, "next_nack = 1'b0;", 'next_nack = nack;')},
    25: {'holdloss': (123, 'next_nack = nack_reg;', "next_nack = 1'b0;"),
         'clearloss': (137, "next_nack = 1'b0;", 'next_nack = nack_reg;')}}
KINDS = ('evt', 'copy', 'holdloss', 'clearloss', 'healthy')
API_MODEL = 'deepseek-flash'
TEMPERATURE, MAX_TOKENS, SAMPLES, MAX_EDITS = 0.7, 2000, 3, 10
NO_MEMORY = 'Memory: no applicable validated knowledge.'
FAMILY_MEANING = ('Mechanism meaning: a NACK / missed-acknowledge event must set a status output that holds '
                  'until it is cleared.')
FORBIDDEN_IN_REQUEST = ('AUG_', 'tb_spec_nack', 'i2c_nack_master_ref', "7'h4B", "7'h2A", 'qual-dev')
SYSTEM = ('You are a hardware design engineer repairing Verilog/SystemVerilog RTL. You cannot run tools. '
          'Answer with exactly one json object and nothing else: either {"action": "no_action"} or '
          '{"action": "edit", "edits": [{"search": "<exact text copied from the file>", "replace": "<new text>"}]}. '
          'Each search text must occur exactly once in the file; edits are applied in order. Keep the module '
          'name, parameters and ports unchanged and change only what the fix requires.')
SYMPTOM = ('Symptom report: The NACK-status regression suite reports a failure for this design. Fix the design if '
           'needed; if it already meets the specification, return it unchanged.')
NACK = r'[A-Za-z_]*nack[A-Za-z_0-9]*'
ROLE_PATTERNS = (('OUTPUT_DRIVE', re.compile(r'^\s*assign\s+nack\s*=')),
                 ('STATUS_REGISTER_LINK', re.compile(rf'(?<![\w.]){NACK}\s*<=\s*[A-Za-z_]\w*\s*;')),
                 ('HOLD_TERM', re.compile(rf'(?<![\w.]){NACK}\s*=\s*{NACK}\s*;')),
                 ('CLEAR_TERM', re.compile(rf"(?<![\w.]){NACK}\s*<?=\s*(\d+'[bdh]0+|0)\s*;")),
                 ('EVENT_SET_TERM', re.compile(rf"(?<![\w.]){NACK}\s*<?=\s*(\d+'[bdh]\d+|\d+)\s*;")),
                 ('OTHER_NACK', re.compile(rf'(?<![\w.]){NACK}\s*(<=|=(?!=))')))
PASS = {'target': 'PASS', 'preservation': 'PASS'}


def h(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


# ---------------------------------------------------------------- tasks
def tasks():
    out = {}
    for d in (7, 25):
        clean_path, clean_sha = CLEAN[d]
        clean = clean_path.read_text()
        assert h(clean) == clean_sha
        m = EVT_SITE.search(clean)
        out['p3c_%03d_evt' % d] = clean[:m.start()] + m[0].replace("1'b1", "1'b0") + clean[m.end():]
        copy_path, copy_sha = COPY[d]
        copy = copy_path.read_text()
        assert h(copy) == copy_sha
        out['p3c_%03d_copy' % d] = copy
        lines = clean.splitlines(True)
        for kind, (ln, old, new) in LINE_FAULTS[d].items():
            assert lines[ln - 1].strip() == old, (d, kind)
            out['p3c_%03d_%s' % (d, kind)] = ''.join(lines[:ln - 1] + [lines[ln - 1].replace(old, new)] + lines[ln:])
        out['p3c_%03d_healthy' % d] = clean
    return out


# ---------------------------------------------------------------- memory rendering
@functools.lru_cache(maxsize=1)
def exemplars():
    """Verified TRAIN before/after diffs, re-derived by the frozen action and checked against the pinned candidates."""
    blocks = []
    for n, case in enumerate(raw.CASE_ORDER, 1):
        source = evidence.train_source(case)
        candidate, _ = apply_rtl_action(source, payload_from_source_i2c_v3(source, raw.SOURCES[case]['public_context']))
        if not raw._equal(evidence._source_hashes(case, candidate), raw.SOURCES[case]['candidate_sha256']):
            raise SystemExit('exemplar %s does not reproduce the pinned TRAIN candidate' % case)
        pairs = ([(r, decode_closure(source)[r], decode_closure(candidate)[r]) for r in raw.ROLES]
                 if case == 'freecores' else [('design', source, candidate)])
        blocks.append(''.join(udiff(a, b, 'exemplar%d/%s' % (n, r)) for r, a, b in pairs if a != b))
    return tuple(blocks)


def knowledge_rows(conn):
    return [dict(r) for r in conn.execute(
        "select k.* from tehm_mechanism_knowledge k join tehm_mechanism_knowledge_status s "
        "on k.knowledge_id = s.knowledge_id and k.version = s.version where s.status = 'validated' "
        "order by k.knowledge_id").fetchall()]


def render(conn, decision, experiences=()):
    if decision != 'CONSIDER':
        return NO_MEMORY
    rows = knowledge_rows(conn)
    if not rows:
        return NO_MEMORY
    parts = ['Memory: validated mechanism knowledge (verified by independent tests on earlier designs).']
    for k in rows:
        expected = json.loads(k['expected_outcome_json'])
        parts.append('- Mechanism family: %s (evidence level %s, replicated over %d independent source lineages). %s\n'
                     '  Expected outcome after the intervention: target test %s with preservation kept. Obligations: '
                     'the fix must be verified by the target and preservation tests.'
                     % (k['mechanism_family'], k['evidence_level'], len(json.loads(k['support_lineages_json'])),
                        FAMILY_MEANING, expected.get('preferred_outcome')))
    parts.append('Verified repairs from those lineages (fault -> verified fix; different designs, same mechanism):')
    parts.extend(exemplars())
    for n, exp in enumerate(experiences, 1):
        parts.append('Unreplicated experience %d (1 verified repair on another design; causal roles %s):'
                     % (n, ', '.join(exp['roles'])))
        parts.append(exp['diff'])
    return '\n'.join(parts)


def route_all(conns, task_id, plan, acquisitions):
    out = {}
    query = MemoryQuery(query_plan={**plan, 'design_id': task_id})
    for view, conn in conns.items():
        replay = (scoped_learning_replay(conn, campaign_id=acquisitions['campaign_id'],
                                         acquisitions=acquisitions['acquisitions'],
                                         expected_digest=acquisitions['digest']) if view == 'm-plus' else nullcontext())
        with replay:
            out[view] = route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                                     persist_state=False, commit=False).decision
    return out


# ---------------------------------------------------------------- requests, proposals, provider
def messages(source, memory):
    user = ('Specification:\n' + SPEC.read_text() + '\n\n' + memory + '\n\n' + SYMPTOM +
            '\n\nCurrent source file:\n```verilog\n' + source + '```\n\nReply with the json object only.')
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]
    bad = [w for w in FORBIDDEN_IN_REQUEST if w in json.dumps(msgs)]
    if bad:
        raise SystemExit('request hygiene failure: %s' % bad)
    return msgs


def apply_proposal(content, source):
    """Return (candidate_text or None, kind). Deterministic; used by the run and by the audit."""
    try:
        obj = json.loads(content)
    except Exception:
        return None, 'MALFORMED_JSON'
    if not isinstance(obj, dict):
        return None, 'MALFORMED_JSON'
    if obj.get('action') == 'no_action':
        return source, 'no_action'
    edits = obj.get('edits')
    if obj.get('action') != 'edit' or not isinstance(edits, list) or not 0 < len(edits) <= MAX_EDITS:
        return None, 'MALFORMED_PROPOSAL'
    text = source
    for e in edits:
        if not (isinstance(e, dict) and isinstance(e.get('search'), str) and isinstance(e.get('replace'), str)
                and e['search'] and text.count(e['search']) == 1):
            return None, 'MALFORMED_EDIT'
        text = text.replace(e['search'], e['replace'], 1)
    return text, 'edit'


def deepseek_key(path, label):
    blocks, cur = [], {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r'\s*export\s+(DEEPSEEK_[A-Z_]+)=(.*)$', line)
        if not m:
            continue
        name, value = m[1], m[2].strip().strip('"').strip("'")
        if name == 'DEEPSEEK_API_KEY' and cur:
            blocks.append(cur); cur = {}
        cur[name] = value
    if cur:
        blocks.append(cur)
    chosen = [b for b in blocks if b.get('DEEPSEEK_MODEL') == label and b.get('DEEPSEEK_API_KEY')]
    if len(chosen) != 1:
        raise SystemExit('credential block for %s not unique' % label)
    return chosen[0]['DEEPSEEK_API_KEY']


class Provider:
    def __init__(self, gate_path, credentials):
        self.gate = json.loads(Path(gate_path).read_text())
        g = self.gate
        assert g.get('schema') == 'r5-phase3c-agent-gate-v1' and g.get('authorized_by_user') is True
        assert g['driver_sha256'] == h(Path(__file__).read_bytes()) and g['api_model_id'] == API_MODEL
        self.key = deepseek_key(credentials, g['model'])
        self.used, self.calls, self.log = 0, 0, []

    def allowed(self, msgs):
        est = math.ceil(len(json.dumps(msgs)) / 3)
        return self.calls < self.gate['max_calls'] and self.used + est + MAX_TOKENS <= self.gate['max_total_tokens']

    def call(self, msgs, prefix):
        body = json.dumps({'model': API_MODEL, 'temperature': TEMPERATURE, 'max_tokens': MAX_TOKENS,
                           'thinking': {'type': 'disabled'}, 'stream': False,
                           'response_format': {'type': 'json_object'}, 'messages': msgs}).encode()
        req = urllib.request.Request(self.gate['base_url'].rstrip('/') + '/chat/completions', data=body, method='POST',
                                     headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw_bytes, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            raw_bytes, status = exc.read() or str(exc).encode(), exc.code
        except Exception as exc:
            raw_bytes, status = str(exc).encode(), -1
        self.calls += 1
        Path(str(prefix) + '.request.json').write_bytes(body)
        Path(str(prefix) + '.response.json').write_bytes(raw_bytes)
        rec = {'status': status, 'wall_s': round(time.time() - started, 2), 'request_sha256': h(body),
               'response_sha256': h(raw_bytes)}
        content = None
        try:
            data = json.loads(raw_bytes)
            rec['usage'] = data.get('usage', {})
            self.used += int(rec['usage'].get('total_tokens', 0))
            content = data['choices'][0]['message']['content']
        except Exception as exc:
            rec['error'] = type(exc).__name__
        self.log.append(rec)
        return rec, content


# ---------------------------------------------------------------- oracle, roles, diffs
def load_oracle():
    import importlib.util
    path = CTRL / 'oracle/run.py'
    assert h(path.read_bytes()) == ORACLE_RUN_SHA and h((CTRL / 'oracle/tb_spec_nack.sv').read_bytes()) == ORACLE_TB_SHA
    spec = importlib.util.spec_from_file_location('ctrlpop_oracle', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def causal_roles(source, candidate):
    roles = set()
    for line in difflib.unified_diff(source.splitlines(), candidate.splitlines(), lineterm='', n=0):
        if line[:1] != '+' or line[:3] == '+++':  # statements the candidate introduces
            continue
        stmt = line[1:].split('//')[0]
        for role, pat in ROLE_PATTERNS:
            if pat.search(stmt):
                roles.add(role)
                break
    return sorted(roles)


def udiff(a, b, name):
    return ''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), name + ' (fault)',
                                        name + ' (verified fix)', n=3))


# ---------------------------------------------------------------- run
ARM_VIEW = {'A0': 'm-minus', 'A1': 'm-plus', 'A2': 'mremove', 'A1+1': 'm-plus'}


def build_requests(root, conns, plan, acquisitions, design, arms, all_tasks, experiences):
    reqs, renders = {}, {}
    for kind in KINDS:
        tid = 'p3c_%03d_%s' % (design, kind)
        decisions = route_all(conns, tid, plan, acquisitions)
        for arm in (*arms, 'A2'):
            view = ARM_VIEW[arm]
            mem = render(conns[view], decisions[view], experiences if arm == 'A1+1' else ())
            renders[(tid, arm)] = {'route': decisions[view], 'memory_sha256': h(mem), 'memory_bytes': len(mem.encode())}
            reqs[(tid, arm)] = messages(all_tasks[tid], mem)
            d = root / 'requests' / tid
            d.mkdir(parents=True, exist_ok=True)
            base.write(d / (arm + '.messages.json'), reqs[(tid, arm)])
        assert json.dumps(reqs[(tid, 'A2')]) == json.dumps(reqs[(tid, 'A0')]), 'Mremove request differs from M-'
    return reqs, renders


def run_stage(root, provider, oracle, all_tasks, reqs, arms, design, results):
    emitted = {}
    for kind in KINDS:
        tid = 'p3c_%03d_%s' % (design, kind)
        for arm in arms:
            d = root / 'calls' / tid / arm
            d.mkdir(parents=True, exist_ok=True)
            for s in range(1, SAMPLES + 1):
                if not provider.allowed(reqs[(tid, arm)]):
                    emitted[(tid, arm, s)] = {'call': 'SKIPPED_FOR_CAP', 'candidate': None, 'kind': None}
                    continue
                rec, content = provider.call(reqs[(tid, arm)], d / ('s%d' % s))
                cand, kind_ = apply_proposal(content, all_tasks[tid]) if content is not None else (None, 'CALL_FAILED')
                if cand is not None:
                    (d / ('s%d.candidate.txt' % s)).write_text(cand)
                emitted[(tid, arm, s)] = {'call': rec, 'candidate': cand, 'kind': kind_}
    (root / 'emitted').mkdir(exist_ok=True)
    base.write(root / 'emitted' / ('stage%03d.json' % design),
               {'%s/%s/s%d' % k: (h(v['candidate']) if v['candidate'] is not None else None) for k, v in emitted.items()})
    for (tid, arm, s), e in emitted.items():  # oracle only after every candidate of the stage is on disk
        if e['candidate'] is not None:
            verdicts = oracle.evaluate(e['candidate'], root / 'oracles' / tid / arm / ('s%d' % s))['verdicts']
        else:
            verdicts = {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}
        results[(tid, arm, s)] = {
            'kind': e['kind'], 'call': e['call'], 'verdicts': verdicts,
            'candidate_sha256': h(e['candidate']) if e['candidate'] is not None else None,
            'changed': e['candidate'] is not None and e['candidate'] != all_tasks[tid],
            'roles': causal_roles(all_tasks[tid], e['candidate']) if e['candidate'] is not None else []}


def writeback(root, results, all_tasks):
    experiences, log = [], {}
    for kind in KINDS[:-1]:
        tid = 'p3c_007_%s' % kind
        log[tid] = None
        for s in range(1, SAMPLES + 1):
            r = results.get((tid, 'A1', s))
            if not r or r['verdicts'] != PASS or not r['changed'] or not r['roles']:
                continue
            cand = (root / 'calls' / tid / 'A1' / ('s%d.candidate.txt' % s)).read_text()
            experiences.append({'task_id': tid, 'sample': s, 'roles': r['roles'], 'candidate_sha256': h(cand),
                                'source_sha256': h(all_tasks[tid]), 'verdicts': r['verdicts'],
                                'diff': udiff(all_tasks[tid], cand, 'experience/design'),
                                'evidence_level': 'L1_SINGLE_VERIFIED_REPAIR', 'scope': 'research_only_not_admission'})
            log[tid] = {'sample': s, 'roles': r['roles']}
            break
    if experiences:
        dst = root / 'memory-mplus1'
        shutil.copytree(base.MEMORY / 'bundles/m-plus', dst / 'bundle')
        assert base.fsha(dst / 'bundle/closed_loop/tehm.sqlite') == base.fsha(base.MEMORY / 'bundles/m-plus/closed_loop/tehm.sqlite')
        base.write(dst / 'experiences.json', {'schema': 'r5-phase3c-experiences-v1', 'mechanism_family': 'I2C_NACK_STATUS_LATCH',
                                              'records': experiences})
    return experiences, log


def summarize(results):
    rows = {}
    for (tid, arm, s), r in sorted(results.items()):
        row = rows.setdefault('%s/%s' % (tid, arm), {'samples': 0, 'pass': 0, 'malformed': 0, 'changed_failing': 0,
                                                     'skipped': 0, 'tokens': 0, 'roles_of_passing': []})
        row['samples'] += 1
        if r['call'] == 'SKIPPED_FOR_CAP':
            row['skipped'] += 1
            continue
        row['tokens'] += int(r['call'].get('usage', {}).get('total_tokens', 0))
        if r['kind'] and r['kind'].startswith('MALFORMED'):
            row['malformed'] += 1
        if r['verdicts'] == PASS:
            row['pass'] += 1
            row['roles_of_passing'].append(r['roles'])
        elif r['changed']:
            row['changed_failing'] += 1
    return rows


def prepare(root, dry):
    report = json.loads((base.MEMORY / 'research/m0-build-report.json').read_text())
    assert report['report_digest'] == base.REPORT and h(SPEC.read_bytes()) == SPEC_SHA
    assert base.git(base.FROZEN, 'rev-parse', 'HEAD') == base.FROZEN_HEAD and not base.git(base.FROZEN, 'status', '--porcelain')
    root.mkdir(parents=True, exist_ok=False)
    cold = m0.verify(base.MEMORY)
    assert cold['valid'] and cold['report_digest'] == base.REPORT
    base.write(root / 'cold-memory-verify.json', cold)
    all_tasks = tasks()
    (root / 'tasks').mkdir()
    for tid, text in all_tasks.items():
        (root / 'tasks' / (tid + '.v')).write_text(text)
    lock = {'schema': 'r5-phase3c-agent-lock-v1', 'frozen_software_head': base.FROZEN_HEAD,
            'main_repo_head': base.git(base.REPO, 'rev-parse', 'HEAD'),
            'main_repo_clean': not base.git(base.REPO, 'status', '--porcelain', '--untracked-files=all'),
            'driver_sha256': h(Path(__file__).read_bytes()), 'memory_report_digest': base.REPORT,
            'sqlite_before': {v: base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in base.BUNDLES},
            'spec_sha256': SPEC_SHA, 'oracle_tb_sha256': ORACLE_TB_SHA, 'oracle_runner_sha256': ORACLE_RUN_SHA,
            'tasks': {t: h(s) for t, s in all_tasks.items()}, 'model': API_MODEL, 'temperature': TEMPERATURE,
            'max_tokens': MAX_TOKENS, 'samples': SAMPLES, 'mode': 'DRY_RUN_NO_CALLS' if dry else 'LIVE'}
    base.write(root / 'pre_execution_lock.json', lock)
    acquisitions = json.loads((base.MEMORY / 'research/parent-acquisitions.json').read_text())
    return report['train_consumption_preflight']['query_plan'], acquisitions, all_tasks, lock


def dry_run(root):
    plan, acquisitions, all_tasks, _ = prepare(root, True)
    conns = {v: base.reload(v) for v in base.BUNDLES}
    try:
        fake = [{'roles': causal_roles(all_tasks['p3c_007_%s' % k], CLEAN[7][0].read_text()),
                 'diff': udiff(all_tasks['p3c_007_%s' % k], CLEAN[7][0].read_text(), 'experience/design')}
                for k in KINDS[:-1]]
        r7, d7 = build_requests(root, conns, plan, acquisitions, 7, ('A0', 'A1'), all_tasks, ())
        r25, d25 = build_requests(root / 'stage025', conns, plan, acquisitions, 25, ('A0', 'A1', 'A1+1'), all_tasks, fake)
    finally:
        for c in conns.values():
            c.close()
    est = {'%s/%s' % k: math.ceil(len(json.dumps(m)) / 3) for k, m in {**r7, **r25}.items()}
    calling = {k: v for k, v in est.items() if not k.endswith('/A2')}
    out = {'mode': 'DRY_RUN_NO_CALLS', 'renders': {'%s/%s' % k: v for k, v in {**d7, **d25}.items()},
           'input_token_estimates': est, 'max_calls': SAMPLES * len(calling),
           'worst_tokens': SAMPLES * sum(v + MAX_TOKENS for v in calling.values()),
           'restore_roles': {t: causal_roles(all_tasks[t], CLEAN[7 if '_007_' in t else 25][0].read_text())
                             for t in all_tasks if not t.endswith('healthy')},
           'fault_oracle_expectation_note': 'fault verdicts qualified by the 2026-09-29 DEV probe (card section 1)'}
    base.write(root / 'dry-run.json', out)
    print(json.dumps({k: v for k, v in out.items() if k not in ('renders', 'input_token_estimates')}, indent=1))
    print('memory bytes by arm:', sorted({(k.split('/')[1], v['memory_bytes']) for k, v in out['renders'].items()}))
    return 0


def run(gate, credentials, root=ROOT):
    plan, acquisitions, all_tasks, lock = prepare(root, False)
    provider = Provider(gate, credentials)
    oracle = load_oracle()
    conns = {v: base.reload(v) for v in base.BUNDLES}
    renders, results, loop = {}, {}, {}
    try:
        reqs, d = build_requests(root, conns, plan, acquisitions, 7, ('A0', 'A1'), all_tasks, ())
        renders.update(d)
        run_stage(root, provider, oracle, all_tasks, reqs, ('A0', 'A1'), 7, results)
        experiences, loop['writeback'] = writeback(root, results, all_tasks)
        loop['experience_count'] = len(experiences)
        arms = ('A0', 'A1', 'A1+1') if experiences else ('A0', 'A1')
        reqs, d = build_requests(root, conns, plan, acquisitions, 25, arms, all_tasks, experiences)
        renders.update(d)
        run_stage(root, provider, oracle, all_tasks, reqs, arms, 25, results)
    finally:
        for c in conns.values():
            c.close()
    after = {v: base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in base.BUNDLES}
    rows = summarize(results)
    invariants = {'bundles_unchanged': after == lock['sqlite_before'], 'main_repo_clean_at_start': lock['main_repo_clean'],
                  'frozen_worktree_clean': not base.git(base.FROZEN, 'status', '--porcelain'),
                  'mremove_render_equals_m_minus': all(v['memory_sha256'] == renders[(t, 'A0')]['memory_sha256']
                                                       for (t, a), v in renders.items() if a == 'A2'),
                  'within_cap': provider.calls <= provider.gate['max_calls'] and provider.used <= provider.gate['max_total_tokens']}
    receipt = {'schema': 'r5-phase3c-agent-v1', 'lock': lock, 'protocol_valid': all(invariants.values()),
               'invariants': invariants, 'renders': {'%s/%s' % k: v for k, v in renders.items()}, 'rows': rows,
               'loop': loop, 'calls': provider.calls, 'tokens': provider.used, 'call_log': provider.log,
               'results': {'%s/%s/s%d' % k: v for k, v in results.items()}, 'sqlite_after': after,
               'mremove_note': 'A2 requests are byte-identical to A0; A0 samples serve A2 (no separate calls)'}
    base.write(root / 'receipt.json', receipt)
    print(json.dumps({'protocol_valid': receipt['protocol_valid'], 'invariants': invariants, 'calls': provider.calls,
                      'tokens': provider.used, 'loop': loop}, indent=1))
    for k, r in rows.items():
        print('%-28s pass %d/%d malformed %d changed_failing %d skipped %d' % (k, r['pass'], r['samples'], r['malformed'],
                                                                             r['changed_failing'], r['skipped']))
    return 0 if receipt['protocol_valid'] else 1


def audit():
    """Separate-process cold audit: re-derive candidates, verdicts, hygiene, Mremove equality and write-back."""
    rec = json.loads((ROOT / 'receipt.json').read_text())
    oracle_mod = load_oracle()
    all_tasks = tasks()
    checks = {'bundles_unchanged_now': all(base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') ==
                                           rec['lock']['sqlite_before'][v] for v in base.BUNDLES),
              'protocol_valid_recorded': rec['protocol_valid'] is True,
              'tasks_pinned': {t: h(s) for t, s in all_tasks.items()} == rec['lock']['tasks']}
    for p in sorted((ROOT / 'calls').rglob('*.request.json')):
        checks['hygiene:' + str(p.relative_to(ROOT))] = not any(w in p.read_text() for w in FORBIDDEN_IN_REQUEST)
    for key, r in rec['results'].items():
        tid, arm, s = key.split('/')
        if r['call'] == 'SKIPPED_FOR_CAP':
            continue
        resp = ROOT / 'calls' / tid / arm / (s + '.response.json')
        try:
            content = json.loads(resp.read_bytes())['choices'][0]['message']['content']
            cand, _ = apply_proposal(content, all_tasks[tid])
        except Exception:
            cand = None
        checks[key + ':candidate_rederived'] = (h(cand) if cand is not None else None) == r['candidate_sha256']
        od = ROOT / 'oracles' / tid / arm / s
        if cand is None:
            checks[key + ':no_oracle'] = not od.exists()
            continue
        duts = sorted(od.glob('dut.*'))
        checks[key + ':oracle_input'] = len(duts) == 1 and duts[0].read_bytes() == cand.encode()
        res = json.loads((od / 'result.json').read_text())
        stdout = (od / 'sim.stdout.log').read_text(errors='replace') if (od / 'sim.stdout.log').exists() else ''
        derived = oracle_mod.verdicts(res['sim'], stdout) if res['sim'] else {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}
        checks[key + ':verdicts'] = derived == r['verdicts']
        checks[key + ':roles'] = causal_roles(all_tasks[tid], cand) == r['roles']
    for key, v in rec['renders'].items():
        if key.endswith('/A2'):
            checks['mremove_equals_m_minus:' + key] = v['memory_sha256'] == rec['renders'][key[:-2] + 'A0']['memory_sha256']
    if rec['loop'].get('experience_count'):
        exps = json.loads((ROOT / 'memory-mplus1/experiences.json').read_text())['records']
        checks['writeback_bundle_copy_identical'] = (base.fsha(ROOT / 'memory-mplus1/bundle/closed_loop/tehm.sqlite') ==
                                                     rec['lock']['sqlite_before']['m-plus'])
        for e in exps:
            r = rec['results']['%s/A1/s%d' % (e['task_id'], e['sample'])]
            checks['writeback_verified:' + e['task_id']] = r['verdicts'] == PASS and r['changed'] and r['roles'] == e['roles']
    out = {'schema': 'r5-phase3c-agent-cold-audit-v1', 'valid': all(checks.values()), 'check_count': len(checks),
           'failed': sorted(k for k, v in checks.items() if not v), 'checks': checks}
    print(json.dumps({k: v for k, v in out.items() if k != 'checks'}, indent=1))
    return out


def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == 'dry-run':
        return dry_run(Path(a[1]).resolve() / 'p3c-dry')
    if len(a) == 5 and a[0] == 'run' and a[1] == '--gate' and a[3] == '--credentials':
        return run(a[2], a[4])
    if a == ['audit']:
        out = audit()
        base.write(ROOT / 'cold-audit.json', out)
        return 0 if out['valid'] else 1
    raise SystemExit(__doc__)


if __name__ == '__main__':
    raise SystemExit(main())
