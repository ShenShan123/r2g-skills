"""R5 Phase 3b part 1b: agent repair with scoped-guard causal retrieval on the part-1 tasks (development fix-check)
(card memory/evaluation/research_r5_phase3b_part1b_scoped_guards_contract_20260930.md; part 1 r1 used 6dacfb8).

usage:
  r5_phase3b_agent.py dry-run OUT_DIR                   tasks, observer, stores and every request; no calls
  r5_phase3b_agent.py run --gate G --credentials F      registered run into ROOT
  r5_phase3b_agent.py audit                             separate-process cold audit of ROOT
Reuses the 3c agent helpers (r5_phase3_agent) and the frozen part-0 store (r5_phase3b_causal_store).
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import r5_phase3b_causal_store as B  # noqa: E402  (frozen part-0 store, observer, retrieval)

P = B.P
ROOT = P.PILOT / 'agent/p3b-part1b-r1'
P3C = B.P3C
KINDS = ('evtsig', 'selfhold', 'cleardel', 'polarity', 'healthy')
ARMS = ('A0', 'A1', 'A4')
FAULTS = {  # design -> kind -> (line, old statement, new statement or None to delete, sha256 prefix from the DEV probe)
    7: {'evtsig': (209, "next_nack = 1'b1;", 'next_nack = nack;', '0b975917f909ce50'),
        'selfhold': (85, 'nack <= next_nack;', 'nack <= nack;', '3bafbced02bebec2'),
        'cleardel': (126, "next_nack = 1'b0;", None, '599e4e0d9dc2b71d'),
        'polarity': (190, 'if (~sda_i) begin', 'if (sda_i) begin', 'c9fb404eac2cf095')},
    25: {'evtsig': (201, "next_nack = 1'b1;", 'next_nack = nack_reg;', '77bb55493a45f8af'),
         'selfhold': (105, 'nack_reg <= next_nack;', 'nack_reg <= nack_reg;', '8d7c6e411e25df2c'),
         'cleardel': (137, "next_nack = 1'b0;", None, '323324a115b26d37'),
         'polarity': (199, "if (sda_i == 1'b1) begin", "if (sda_i == 1'b0) begin", 'e9c146a44b34938a')}}
OBSERVATION = {
    'NEVER_SET': 'Port observation: in the test stage that addresses a device that does not acknowledge, `nack` never became 1.',
    'SET_NOT_HELD': 'Port observation: in the test stage that addresses a device that does not acknowledge, `nack` became 1 '
                    'but returned to 0 before the end of that stage.',
    'NOT_CLEARED': 'Port observation: `nack` was 1 at the end of the stage that addresses a device that does not acknowledge, '
                   'and was still 1 after the next command was accepted.',
    'NO_ANOMALY': 'Port observation: `nack` behaved as the specification requires in the observed stages.',
    'UNOBSERVED': 'Port observation: the observed stages did not complete, so no observation is available.',
    'SET_ON_ACK': 'Port observation: `nack` became 1 during a transfer to a device that acknowledged its address.'}
SCOPE = {
    'NACK_SAMPLE': ('The observed behaviour points to the NACK sample itself (first node of the path). No verified repair '
                    'for this node is stored; the stored repairs of later nodes do not apply.'),
    'UNOBSERVED': ('The observed stages did not complete; the failure is outside what this Memory\'s observations cover. '
                   'The stored mechanism knowledge does not apply to this situation.'),
    'NO_ANOMALY': 'No anomaly was observed on the stored mechanism; this Memory gives no repair advice.'}


def tasks():
    out = {}
    for d, faults in FAULTS.items():
        clean = P.CLEAN[d][0].read_text()
        assert P.h(clean) == P.CLEAN[d][1]
        lines = clean.splitlines(True)
        for kind, (ln, old, new, prefix) in faults.items():
            assert lines[ln - 1].strip() == old, (d, kind)
            text = ''.join(lines[:ln - 1] + ([] if new is None else [lines[ln - 1].replace(old, new)]) + lines[ln:])
            assert P.h(text).startswith(prefix), (d, kind)
            out['p3b_%03d_%s' % (d, kind)] = text
        out['p3b_%03d_healthy' % d] = clean
    return out


def causal_render(store, role, situation):
    """Card 1b section 1.3: scoped guards and explicit scope statements."""
    head = 'Memory (causal retrieval for the observed situation):'
    path = store.render_active().split('\n')[0]  # causal path summary line only
    if situation == 'UNOBSERVED':
        return head + '\n' + SCOPE['UNOBSERVED']
    if situation == 'NO_ANOMALY' or role is None:
        return '\n'.join([head, SCOPE['NO_ANOMALY']] + store.guards_for(situation))
    if role == 'NACK_SAMPLE':
        return '\n'.join([head, path, SCOPE['NACK_SAMPLE']] + store.guards_for(situation))
    node = (store.render_node(role, situation) if role in store.nodes
            else 'Role %s: %s.' % (role, B.STATEMENT.get(role, '')))
    return '\n'.join([head, path] + store.guards_for(situation) + [node])


def build_store(held):
    other = '025' if held == 7 else '007'
    all3c = P.tasks()
    eps = B.train_episodes() + [e for e in B.p3c_episodes(all3c) if e['design'] == 'ctrlpop:' + other]
    store = B.Store()
    for e in eps:
        store.add(e)
    return store, eps


def messages(source, memory, observation):
    user = ('Specification:\n' + P.SPEC.read_text() + '\n\n' + memory + '\n\n' + P.SYMPTOM + '\n' + observation +
            '\n\nCurrent source file:\n```verilog\n' + source + '```\n\nReply with the json object only.')
    msgs = [{'role': 'system', 'content': P.SYSTEM}, {'role': 'user', 'content': user}]
    bad = [w for w in P.FORBIDDEN_IN_REQUEST if w in json.dumps(msgs)]
    if bad:
        raise SystemExit('request hygiene failure: %s' % bad)
    return msgs


def build_requests(root, all_tasks, symptoms):
    report = json.loads((P.base.MEMORY / 'research/m0-build-report.json').read_text())
    plan = report['train_consumption_preflight']['query_plan']
    acquisitions = json.loads((P.base.MEMORY / 'research/parent-acquisitions.json').read_text())
    conns = {v: P.base.reload(v) for v in P.base.BUNDLES}
    reqs, meta = {}, {}
    try:
        for d in (7, 25):
            store, eps = build_store(d)
            for kind in KINDS:
                tid = 'p3b_%03d_%s' % (d, kind)
                src, sym = all_tasks[tid], symptoms[tid]
                decision = P.route_all(conns, tid, plan, acquisitions)
                m_plus = P.render(conns['m-plus'], decision['m-plus'])
                retr = B.retrieve('causal', store, eps, src, sym)
                memories = {'A0': P.render(conns['m-minus'], decision['m-minus']), 'A1': m_plus,
                            'A4': causal_render(store, retr['role'], sym)}
                obs = OBSERVATION[sym]
                for arm in ARMS:
                    reqs[(tid, arm)] = messages(src, memories[arm], obs)
                    rd = root / 'requests' / tid
                    rd.mkdir(parents=True, exist_ok=True)
                    P.base.write(rd / (arm + '.messages.json'), reqs[(tid, arm)])
                meta[tid] = {'symptom': sym, 'retrieved_role': retr['role'], 'candidates': retr.get('candidates'),
                             'route_m_plus': decision['m-plus'], 'route_m_minus': decision['m-minus'],
                             'memory_bytes': {a: len(memories[a].encode()) for a in ARMS}}
    finally:
        for c in conns.values():
            c.close()
    return reqs, meta


def calling(meta):
    order = []
    for d in (7, 25):
        for kind in KINDS:
            tid = 'p3b_%03d_%s' % (d, kind)
            for arm in ARMS:
                order.append((tid, arm))
    return order


class Provider(P.Provider):
    def __init__(self, gate_path, credentials):  # pylint: disable=super-init-not-called
        self.gate = json.loads(Path(gate_path).read_text())
        g = self.gate
        assert g.get('schema') == 'r5-phase3b-part1b-gate-v1' and g.get('authorized_by_user') is True
        assert g['driver_sha256'] == P.h(Path(__file__).read_bytes())
        assert g['helper_sha256'] == {'r5_phase3_agent.py': P.h(Path(P.__file__).read_bytes()),
                                      'r5_phase3b_causal_store.py': P.h(Path(B.__file__).read_bytes())}
        assert g['api_model_id'] == P.API_MODEL
        self.key = P.deepseek_key(credentials, g['model'])
        self.used, self.calls, self.log = 0, 0, []

    def call(self, msgs, prefix):
        """Card 1b section 1.5: up to 2 retries on transport failure only; every attempt is kept."""
        attempts, content = [], None
        for a in range(1, 4):
            if a > 1 and not self.allowed(msgs):
                break
            rec, content = P.Provider.call(self, msgs, Path('%s.try%d' % (prefix, a)))
            attempts.append(rec)
            if not (rec['status'] == -1 or rec['status'] >= 500):
                break
        for ext in ('.request.json', '.response.json'):
            shutil.copyfile('%s.try%d%s' % (prefix, len(attempts), ext), str(prefix) + ext)
        return dict(attempts[-1], attempts=len(attempts), attempt_log=attempts), content


def prepare(root, dry):
    report = json.loads((P.base.MEMORY / 'research/m0-build-report.json').read_text())
    assert report['report_digest'] == P.base.REPORT and P.h(P.SPEC.read_bytes()) == P.SPEC_SHA
    assert P.base.git(P.base.FROZEN, 'rev-parse', 'HEAD') == P.base.FROZEN_HEAD
    assert not P.base.git(P.base.FROZEN, 'status', '--porcelain')
    root.mkdir(parents=True, exist_ok=False)
    cold = P.m0.verify(P.base.MEMORY)
    assert cold['valid'] and cold['report_digest'] == P.base.REPORT
    P.base.write(root / 'cold-memory-verify.json', cold)
    all_tasks = tasks()
    (root / 'tasks').mkdir()
    for tid, text in all_tasks.items():
        (root / 'tasks' / (tid + '.v')).write_text(text)
    symptoms = {tid: B.observe(text, root / 'observer' / tid)['class'] for tid, text in sorted(all_tasks.items())}
    reqs, meta = build_requests(root, all_tasks, symptoms)
    lock = {'schema': 'r5-phase3b-part1-lock-v1', 'mode': 'DRY_RUN_NO_CALLS' if dry else 'LIVE',
            'main_repo_head': P.base.git(P.base.REPO, 'rev-parse', 'HEAD'),
            'main_repo_clean': not P.base.git(P.base.REPO, 'status', '--porcelain', '--untracked-files=all'),
            'driver_sha256': P.h(Path(__file__).read_bytes()),
            'helper_sha256': {'r5_phase3_agent.py': P.h(Path(P.__file__).read_bytes()),
                              'r5_phase3b_causal_store.py': P.h(Path(B.__file__).read_bytes())},
            'sqlite_before': {v: P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in P.base.BUNDLES},
            'tasks': {t: P.h(s) for t, s in all_tasks.items()}, 'symptoms': symptoms, 'meta': meta,
            'model': P.API_MODEL, 'temperature': P.TEMPERATURE, 'max_tokens': P.MAX_TOKENS, 'samples': P.SAMPLES,
            'planned_calls': P.SAMPLES * len(calling(meta))}
    P.base.write(root / 'pre_execution_lock.json', lock)
    return all_tasks, reqs, meta, lock


def dry_run(root):
    all_tasks, reqs, meta, lock = prepare(root, True)
    est = sum(len(json.dumps(reqs[k])) // 3 + P.MAX_TOKENS for k in calling(meta)) * P.SAMPLES
    print(json.dumps({'planned_calls': lock['planned_calls'], 'worst_tokens': est,
                      'meta': {t: (m['symptom'], m['retrieved_role'], m['memory_bytes']) for t, m in meta.items()}}, indent=1))
    return 0


def run(gate, credentials, root=ROOT):
    all_tasks, reqs, meta, lock = prepare(root, False)
    provider = Provider(gate, credentials)
    oracle = P.load_oracle()
    emitted = {}
    for tid, arm in calling(meta):
        d = root / 'calls' / tid / arm
        d.mkdir(parents=True, exist_ok=True)
        for s in range(1, P.SAMPLES + 1):
            if not provider.allowed(reqs[(tid, arm)]):
                emitted[(tid, arm, s)] = {'call': 'SKIPPED_FOR_CAP', 'candidate': None, 'kind': None}
                continue
            rec, content = provider.call(reqs[(tid, arm)], d / ('s%d' % s))
            cand, kind = P.apply_proposal(content, all_tasks[tid]) if content is not None else (None, 'CALL_FAILED')
            if cand is not None:
                (d / ('s%d.candidate.txt' % s)).write_text(cand)
            emitted[(tid, arm, s)] = {'call': rec, 'candidate': cand, 'kind': kind}
    P.base.write(root / 'candidates-emitted.json',
                 {'%s/%s/s%d' % k: (P.h(v['candidate']) if v['candidate'] is not None else None) for k, v in emitted.items()})
    results = {}
    for (tid, arm, s), e in emitted.items():  # oracle only after every candidate is on disk
        v = (oracle.evaluate(e['candidate'], root / 'oracles' / tid / arm / ('s%d' % s))['verdicts']
             if e['candidate'] is not None else {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'})
        results['%s/%s/s%d' % (tid, arm, s)] = {
            'kind': e['kind'], 'call': e['call'], 'verdicts': v,
            'candidate_sha256': P.h(e['candidate']) if e['candidate'] is not None else None,
            'changed': e['candidate'] is not None and e['candidate'] != all_tasks[tid],
            'roles': B.roles_of(all_tasks[tid], e['candidate'], sides='+-') if e['candidate'] is not None else []}
    rows = summarize(results)
    after = {v: P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in P.base.BUNDLES}
    invariants = {'bundles_unchanged': after == lock['sqlite_before'], 'main_repo_clean_at_start': lock['main_repo_clean'],
                  'within_cap': provider.calls <= provider.gate['max_calls'] and provider.used <= provider.gate['max_total_tokens']}
    receipt = {'schema': 'r5-phase3b-part1b-v1', 'lock': lock, 'protocol_valid': all(invariants.values()),
               'invariants': invariants, 'results': results, 'rows': rows, 'calls': provider.calls,
               'tokens': provider.used, 'call_log': provider.log}
    P.base.write(root / 'receipt.json', receipt)
    print(json.dumps({'protocol_valid': receipt['protocol_valid'], 'calls': provider.calls, 'tokens': provider.used,
                      'summary': rows['_summary']}, indent=1))
    for k, r in sorted(rows.items()):
        if k != '_summary':
            print('%-26s pass %d/%d harmful %d malformed %d' % (k, r['pass'], r['n'], r['harmful'], r['malformed']))
    return 0 if receipt['protocol_valid'] else 1


def summarize(results):
    rows = {}
    for key, r in results.items():
        tid, arm, _ = key.split('/')
        row = rows.setdefault('%s/%s' % (tid, arm), {'n': 0, 'pass': 0, 'harmful': 0, 'malformed': 0, 'skipped': 0})
        row['n'] += 1
        if r['call'] == 'SKIPPED_FOR_CAP':
            row['skipped'] += 1
            continue
        row['pass'] += r['verdicts'] == P.PASS
        row['malformed'] += bool(r['kind'] and r['kind'].startswith('MALFORMED'))
        row['harmful'] += tid.endswith('_healthy') and r['changed'] and r['verdicts'] != P.PASS
    classes = {'retrieval_correct': ('evtsig', 'cleardel'), 'retrieval_wrong': ('selfhold',),
               'out_of_family': ('polarity',), 'healthy': ('healthy',)}
    summary = {}
    for arm in ARMS:
        summary[arm] = {}
        for cls, kinds in classes.items():
            cells = [rows['p3b_%03d_%s/%s' % (d, k, arm)] for d in (7, 25) for k in kinds]
            key = 'harmful' if cls == 'healthy' else 'pass'
            summary[arm][cls] = '%d/%d' % (sum(c[key] for c in cells), sum(c['n'] for c in cells))
    rows['_summary'] = summary
    return rows


def audit():
    rec = json.loads((ROOT / 'receipt.json').read_text())
    all_tasks = tasks()
    oracle_mod = P.load_oracle()
    symptoms = {t: B.classify_obs((ROOT / 'observer' / t / 'sim.log').read_text())['class'] for t in sorted(all_tasks)}
    checks = {'tasks_pinned': {t: P.h(s) for t, s in all_tasks.items()} == rec['lock']['tasks'],
              'symptoms_rederived': symptoms == rec['lock']['symptoms'],
              'bundles_unchanged_now': all(P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') ==
                                           rec['lock']['sqlite_before'][v] for v in P.base.BUNDLES)}
    for p in sorted((ROOT / 'calls').rglob('s[0-9].request.json')):
        body = json.loads(p.read_bytes())
        tid, arm = p.parent.parent.name, p.parent.name
        saved = json.loads((ROOT / 'requests' / tid / (arm + '.messages.json')).read_text())
        checks['request_matches_built:' + str(p.relative_to(ROOT))] = body['messages'] == saved
        checks['hygiene:' + str(p.relative_to(ROOT))] = not any(w in p.read_text() for w in P.FORBIDDEN_IN_REQUEST)
    for key, r in rec['results'].items():
        tid, arm, s = key.split('/')
        if r['call'] == 'SKIPPED_FOR_CAP':
            continue
        try:
            content = json.loads((ROOT / 'calls' / tid / arm / (s + '.response.json')).read_bytes())['choices'][0]['message']['content']
            cand, _ = P.apply_proposal(content, all_tasks[tid])
        except Exception:
            cand = None
        checks[key + ':candidate'] = (P.h(cand) if cand is not None else None) == r['candidate_sha256']
        od = ROOT / 'oracles' / tid / arm / s
        if cand is None:
            checks[key + ':no_oracle'] = not od.exists()
            continue
        duts = sorted(od.glob('dut.*'))
        res = json.loads((od / 'result.json').read_text())
        stdout = (od / 'sim.stdout.log').read_text(errors='replace') if (od / 'sim.stdout.log').exists() else ''
        derived = oracle_mod.verdicts(res['sim'], stdout) if res['sim'] else {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}
        checks[key + ':oracle'] = len(duts) == 1 and duts[0].read_bytes() == cand.encode() and derived == r['verdicts']
    out = {'schema': 'r5-phase3b-part1b-cold-audit-v1', 'valid': all(checks.values()), 'check_count': len(checks),
           'failed': sorted(k for k, v in checks.items() if not v), 'checks': checks}
    print(json.dumps({k: v for k, v in out.items() if k != 'checks'}, indent=1))
    return out


def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == 'dry-run':
        return dry_run(Path(a[1]).resolve() / 'p3b1b-dry')
    if len(a) == 5 and a[0] == 'run' and a[1] == '--gate' and a[3] == '--credentials':
        return run(a[2], a[4])
    if a == ['audit']:
        out = audit()
        P.base.write(ROOT / 'cold-audit.json', out)
        return 0 if out['valid'] else 1
    raise SystemExit(__doc__)


if __name__ == '__main__':
    raise SystemExit(main())
