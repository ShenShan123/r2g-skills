"""R5 Phase 3b part 2: masked multi-site and misleading faults, up to 3 rounds with public feedback and iterative
causal re-retrieval (card memory/evaluation/research_r5_phase3b_part2_masked_faults_contract_20260930.md).

usage:
  r5_phase3b_multisite.py dry-run OUT_DIR                 tasks, observer, stores and round-1 requests; no calls
  r5_phase3b_multisite.py run --gate G --credentials F    registered run into ROOT
  r5_phase3b_multisite.py audit                           separate-process cold audit of ROOT
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import r5_phase3b_agent as A  # noqa: E402  (part-1b render, observations, store builder, retrying provider)

B, P = A.B, A.P
ROOT = P.PILOT / 'agent/p3b-part2-r1'
ARMS = ('A0', 'A1', 'A4', 'A5')
KINDS = ('holdclear', 'evtlink', 'triple', 'stopclear', 'healthy')
ROUNDS = 3
SITES = {7: {'hold': (104, 'next_nack = nack;', "next_nack = 1'b0;"),
             'clear': (126, "next_nack = 1'b0;", 'next_nack = nack;'),
             'evt': (209, "next_nack = 1'b1;", "next_nack = 1'b0;"),
             'link': (85, 'nack <= next_nack;', 'nack <= nack;'),
             'stop': (326, "next_done = 1'b1;", "next_done = 1'b1;\n                        next_nack = 1'b0;")},
         25: {'hold': (123, 'next_nack = nack_reg;', "next_nack = 1'b0;"),
              'clear': (137, "next_nack = 1'b0;", 'next_nack = nack_reg;'),
              'evt': (201, "next_nack = 1'b1;", "next_nack = 1'b0;"),
              'link': (105, 'nack_reg <= next_nack;', 'nack_reg <= nack_reg;'),
              'stop': (351, "next_done = 1'b1;", "next_done = 1'b1;\n                next_nack = 1'b0;")}}
FAULT_SITES = {'holdclear': ('hold', 'clear'), 'evtlink': ('evt', 'link'), 'triple': ('evt', 'hold', 'clear'),
               'stopclear': ('stop',)}
PREFIX = {(7, 'holdclear'): '69adda00c6a9af79', (7, 'evtlink'): '1e8755b9781439a6', (7, 'triple'): 'e0f9f6fc6836c2f0',
          (7, 'stopclear'): 'c7ff315ada0ba7f5', (25, 'holdclear'): '4ce6bf251a1c4151', (25, 'evtlink'): 'd776df56f901fa32',
          (25, 'triple'): '2896882e7f67a71e', (25, 'stopclear'): 'fd1366aa63a4435c'}


def tasks():
    out = {}
    for d in (7, 25):
        clean = P.CLEAN[d][0].read_text()
        assert P.h(clean) == P.CLEAN[d][1]
        for kind, sites in FAULT_SITES.items():
            ls = clean.splitlines(True)
            for site in sites:
                ln, old, new = SITES[d][site]
                assert ls[ln - 1].strip() == old, (d, kind, site)
                ls[ln - 1] = ls[ln - 1].replace(old, new)
            text = ''.join(ls)
            assert P.h(text).startswith(PREFIX[(d, kind)]), (d, kind)
            out['p3b2_%03d_%s' % (d, kind)] = text
        out['p3b2_%03d_healthy' % d] = clean
    return out


class Provider(A.Provider):
    def __init__(self, gate_path, credentials):  # pylint: disable=super-init-not-called
        self.gate = json.loads(Path(gate_path).read_text())
        g = self.gate
        assert g.get('schema') == 'r5-phase3b-part2-gate-v1' and g.get('authorized_by_user') is True
        assert g['driver_sha256'] == P.h(Path(__file__).read_bytes())
        assert g['helper_sha256'] == helper_shas() and g['api_model_id'] == P.API_MODEL
        self.key = P.deepseek_key(credentials, g['model'])
        self.used, self.calls, self.log = 0, 0, []


def helper_shas():
    return {Path(m.__file__).name: P.h(Path(m.__file__).read_bytes()) for m in (A, B, P)}


def memory_for(arm, ctx, source, symptom):
    """Memory section for an arm; the causal part is re-retrieved for the current source and observation."""
    if arm == 'A0':
        return ctx['m_minus']
    if arm == 'A1':
        return ctx['m_plus']
    role = B.retrieve('causal', ctx['store'], ctx['eps'], source, symptom)['role']
    causal = A.causal_render(ctx['store'], role, symptom)
    return causal if arm == 'A4' else ctx['m_plus'] + '\n\n' + causal


def feedback(verdicts, symptom, memory, arm):
    text = ('Your proposal was evaluated: target=%s, preservation=%s. %s'
            % (verdicts['target'], verdicts['preservation'], A.OBSERVATION[symptom]))
    if arm in ('A4', 'A5'):
        text += '\n\n' + memory
    text += '\nYour edits now apply to your current candidate file. Reply with one json proposal.'
    return text


def contexts():
    report = json.loads((P.base.MEMORY / 'research/m0-build-report.json').read_text())
    plan = report['train_consumption_preflight']['query_plan']
    acquisitions = json.loads((P.base.MEMORY / 'research/parent-acquisitions.json').read_text())
    conns = {v: P.base.reload(v) for v in P.base.BUNDLES}
    ctx = {}
    try:
        for d in (7, 25):
            store, eps = A.build_store(d)
            decision = P.route_all(conns, 'p3b2_%03d' % d, plan, acquisitions)
            ctx[d] = {'store': store, 'eps': eps, 'm_minus': P.render(conns['m-minus'], decision['m-minus']),
                      'm_plus': P.render(conns['m-plus'], decision['m-plus'])}
    finally:
        for c in conns.values():
            c.close()
    return ctx


def prepare(root, dry):
    report = json.loads((P.base.MEMORY / 'research/m0-build-report.json').read_text())
    assert report['report_digest'] == P.base.REPORT and P.h(P.SPEC.read_bytes()) == P.SPEC_SHA
    assert P.base.git(P.base.FROZEN, 'rev-parse', 'HEAD') == P.base.FROZEN_HEAD
    root.mkdir(parents=True, exist_ok=False)
    cold = P.m0.verify(P.base.MEMORY)
    assert cold['valid']
    P.base.write(root / 'cold-memory-verify.json', cold)
    all_tasks = tasks()
    symptoms = {t: B.observe(s, root / 'observer' / t)['class'] for t, s in sorted(all_tasks.items())}
    ctx = contexts()
    lock = {'schema': 'r5-phase3b-part2-lock-v1', 'mode': 'DRY_RUN_NO_CALLS' if dry else 'LIVE',
            'main_repo_head': P.base.git(P.base.REPO, 'rev-parse', 'HEAD'),
            'main_repo_clean': not P.base.git(P.base.REPO, 'status', '--porcelain', '--untracked-files=all'),
            'driver_sha256': P.h(Path(__file__).read_bytes()), 'helper_sha256': helper_shas(),
            'sqlite_before': {v: P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in P.base.BUNDLES},
            'tasks': {t: P.h(s) for t, s in all_tasks.items()}, 'symptoms': symptoms, 'arms': ARMS, 'rounds': ROUNDS,
            'samples': P.SAMPLES, 'model': P.API_MODEL, 'temperature': P.TEMPERATURE}
    P.base.write(root / 'pre_execution_lock.json', lock)
    return all_tasks, symptoms, ctx, lock


def dry_run(root):
    all_tasks, symptoms, ctx, lock = prepare(root, True)
    sizes = {}
    for tid, src in sorted(all_tasks.items()):
        d = int(tid.split('_')[1])
        for arm in ARMS:
            msgs = A.messages(src, memory_for(arm, ctx[d], src, symptoms[tid]), A.OBSERVATION[symptoms[tid]])
            (root / 'requests' / tid).mkdir(parents=True, exist_ok=True)
            P.base.write(root / 'requests' / tid / (arm + '.r1.messages.json'), msgs)
            sizes['%s/%s' % (tid, arm)] = len(json.dumps(msgs)) // 3
    print(json.dumps({'symptoms': symptoms, 'max_r1_tokens': max(sizes.values()),
                      'max_calls': 8 * len(ARMS) * P.SAMPLES * ROUNDS + 2 * len(ARMS) * P.SAMPLES}, indent=1))
    return 0


def run(gate, credentials, root=ROOT):
    all_tasks, symptoms, ctx, lock = prepare(root, False)
    provider = Provider(gate, credentials)
    oracle = P.load_oracle()
    chains = {}
    for tid, src in sorted(all_tasks.items()):
        d = int(tid.split('_')[1])
        for arm in ARMS:
            for s in range(1, P.SAMPLES + 1):
                mem = memory_for(arm, ctx[d], src, symptoms[tid])
                chains[(tid, arm, s)] = {'design': d, 'current': src, 'symptom': symptoms[tid], 'done': False,
                                         'msgs': A.messages(src, mem, A.OBSERVATION[symptoms[tid]]), 'rounds': []}
    for rnd in range(1, ROUNDS + 1):
        active = [k for k, c in chains.items() if not c['done'] and (rnd == 1 or not k[0].endswith('_healthy'))]
        emitted = {}
        for key in active:  # emit every candidate of this round before any oracle run
            tid, arm, s = key
            c = chains[key]
            d = root / 'calls' / tid / arm / ('s%d' % s)
            d.mkdir(parents=True, exist_ok=True)
            P.base.write(d / ('r%d.messages.json' % rnd), c['msgs'])
            if not provider.allowed(c['msgs']):
                emitted[key] = ('SKIPPED_FOR_CAP', None, None, None)
                continue
            rec, content = provider.call(c['msgs'], d / ('r%d' % rnd))
            cand, kind = P.apply_proposal(content, c['current']) if content is not None else (None, 'CALL_FAILED')
            if cand is not None:
                (d / ('r%d.candidate.txt' % rnd)).write_text(cand)
            emitted[key] = (rec, content, cand, kind)
        (root / 'emitted').mkdir(exist_ok=True)
        P.base.write(root / 'emitted' / ('round%d.json' % rnd),
                     {'%s/%s/s%d' % k: (P.h(v[2]) if v[2] is not None else None) for k, v in emitted.items()})
        for key, (rec, content, cand, kind) in emitted.items():
            tid, arm, s = key
            c = chains[key]
            if cand is None:
                c['rounds'].append({'round': rnd, 'kind': kind if rec != 'SKIPPED_FOR_CAP' else 'SKIPPED_FOR_CAP',
                                    'call': rec, 'verdicts': {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'},
                                    'candidate_sha256': None})
                c['done'] = True
                continue
            base_dir = root / 'oracles' / tid / arm / ('s%d' % s) / ('r%d' % rnd)
            verdicts = oracle.evaluate(cand, base_dir)['verdicts']
            row = {'round': rnd, 'kind': kind, 'call': rec, 'verdicts': verdicts, 'candidate_sha256': P.h(cand),
                   'changed_from_task': cand != all_tasks[tid],
                   'roles': B.roles_of(all_tasks[tid], cand, sides='+-')}
            c['rounds'].append(row)
            if verdicts == P.PASS or tid.endswith('_healthy') or rnd == ROUNDS:
                c['done'] = True
                continue
            sym = B.observe(cand, root / 'observer-rounds' / tid / arm / ('s%d' % s) / ('r%d' % rnd))['class']
            row['next_symptom'] = sym
            mem = memory_for(arm, ctx[c['design']], cand, sym)
            fb = feedback(verdicts, sym, mem, arm)
            c['msgs'] = c['msgs'] + [{'role': 'assistant', 'content': content}, {'role': 'user', 'content': fb}]
            bad = [w for w in P.FORBIDDEN_IN_REQUEST if w in json.dumps(c['msgs'])]
            assert not bad, bad
            c['current'] = cand
    results = {'%s/%s/s%d' % k: {'rounds': c['rounds']} for k, c in chains.items()}
    rows = summarize(results)
    after = {v: P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in P.base.BUNDLES}
    invariants = {'bundles_unchanged': after == lock['sqlite_before'], 'main_repo_clean_at_start': lock['main_repo_clean'],
                  'within_cap': provider.calls <= provider.gate['max_calls'] and provider.used <= provider.gate['max_total_tokens']}
    receipt = {'schema': 'r5-phase3b-part2-v1', 'lock': lock, 'protocol_valid': all(invariants.values()),
               'invariants': invariants, 'results': results, 'rows': rows, 'calls': provider.calls,
               'tokens': provider.used, 'call_log': provider.log}
    P.base.write(root / 'receipt.json', receipt)
    print(json.dumps({'protocol_valid': receipt['protocol_valid'], 'calls': provider.calls, 'tokens': provider.used,
                      'summary': rows['_summary']}, indent=1))
    for k, r in sorted(rows.items()):
        if k != '_summary':
            print('%-26s repaired %d/%d  @1 %d  harmful %d  rounds %s' % (k, r['repaired'], r['n'], r['at1'], r['harmful'], r['rounds']))
    return 0 if receipt['protocol_valid'] else 1


def summarize(results):
    rows = {}
    for key, r in results.items():
        tid, arm, _ = key.split('/')
        row = rows.setdefault('%s/%s' % (tid, arm), {'n': 0, 'repaired': 0, 'at1': 0, 'harmful': 0, 'rounds': [],
                                                     'transport_failed': 0})
        rs = r['rounds']
        row['n'] += 1
        row['rounds'].append(len(rs))
        ok = [x for x in rs if x['verdicts'] == P.PASS]
        row['repaired'] += bool(ok) and not tid.endswith('_healthy')
        row['at1'] += bool(rs) and rs[0]['verdicts'] == P.PASS and not tid.endswith('_healthy')
        row['harmful'] += bool(tid.endswith('_healthy') and rs and rs[0].get('changed_from_task') and rs[0]['verdicts'] != P.PASS)
        row['transport_failed'] += any(x['kind'] == 'CALL_FAILED' for x in rs)
    summary = {}
    for arm in ARMS:
        faults = [v for k, v in rows.items() if k.endswith('/' + arm) and '_healthy' not in k]
        healthy = [v for k, v in rows.items() if k.endswith('/' + arm) and '_healthy' in k]
        summary[arm] = {'repaired_within_3': '%d/%d' % (sum(v['repaired'] for v in faults), sum(v['n'] for v in faults)),
                        'repaired_at_1': '%d/%d' % (sum(v['at1'] for v in faults), sum(v['n'] for v in faults)),
                        'harmful_healthy': '%d/%d' % (sum(v['harmful'] for v in healthy), sum(v['n'] for v in healthy)),
                        'transport_failed_chains': sum(v['transport_failed'] for v in faults + healthy)}
    rows['_summary'] = summary
    return rows


def finalize(root=ROOT):
    """Rebuild results from the raw on-disk evidence of a run whose post-processing crashed (no model calls).

    Used once for p3b-part2-r1: every call, oracle and observation had completed; summarize() then raised a
    TypeError before receipt.json was written. The rebuild reads only files the run itself wrote."""
    lock = json.loads((root / 'pre_execution_lock.json').read_text())
    all_tasks = tasks()
    assert {t: P.h(s) for t, s in all_tasks.items()} == lock['tasks']
    results, calls, tokens = {}, 0, 0
    for tid in sorted(all_tasks):
        for arm in ARMS:
            for s in range(1, P.SAMPLES + 1):
                d = root / 'calls' / tid / arm / ('s%d' % s)
                current, rounds = all_tasks[tid], []
                for rnd in range(1, ROUNDS + 1):
                    if not (d / ('r%d.messages.json' % rnd)).exists():
                        break
                    tries = sorted(d.glob('r%d.try*.response.json' % rnd))
                    calls += len(tries)
                    for t in tries:
                        try:
                            tokens += int(json.loads(t.read_bytes()).get('usage', {}).get('total_tokens', 0))
                        except Exception:
                            pass
                    resp = d / ('r%d.response.json' % rnd)
                    if not resp.exists():
                        rounds.append({'round': rnd, 'kind': 'SKIPPED_FOR_CAP', 'call': 'SKIPPED_FOR_CAP',
                                       'verdicts': {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}, 'candidate_sha256': None})
                        break
                    try:
                        content = json.loads(resp.read_bytes())['choices'][0]['message']['content']
                        cand, kind = P.apply_proposal(content, current)
                    except Exception:
                        cand, kind = None, 'CALL_FAILED'
                    call = {'attempts': len(tries), 'response_sha256': P.h(resp.read_bytes())}
                    if cand is None:
                        rounds.append({'round': rnd, 'kind': kind, 'call': call, 'candidate_sha256': None,
                                       'verdicts': {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}})
                        break
                    res = json.loads((root / 'oracles' / tid / arm / ('s%d' % s) / ('r%d' % rnd) / 'result.json').read_text())
                    row = {'round': rnd, 'kind': kind, 'call': call, 'verdicts': res['verdicts'],
                           'candidate_sha256': P.h(cand), 'changed_from_task': cand != all_tasks[tid],
                           'roles': B.roles_of(all_tasks[tid], cand, sides='+-')}
                    ob = root / 'observer-rounds' / tid / arm / ('s%d' % s) / ('r%d' % rnd) / 'sim.log'
                    if (root / 'observer-rounds' / tid / arm / ('s%d' % s) / ('r%d' % rnd)).exists():
                        row['next_symptom'] = B.classify_obs(ob.read_text() if ob.exists() else '')['class']
                    rounds.append(row)
                    current = cand
                results['%s/%s/s%d' % (tid, arm, s)] = {'rounds': rounds}
    rows = summarize(results)
    after = {v: P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in P.base.BUNDLES}
    gate = json.loads((P.PILOT / 'agent/gate-p3b2-r1.json').read_text())
    invariants = {'bundles_unchanged': after == lock['sqlite_before'], 'main_repo_clean_at_start': lock['main_repo_clean'],
                  'within_cap': calls <= gate['max_calls'] and tokens <= gate['max_total_tokens']}
    receipt = {'schema': 'r5-phase3b-part2-v1', 'lock': lock, 'protocol_valid': all(invariants.values()),
               'invariants': invariants, 'results': results, 'rows': rows, 'calls': calls, 'tokens': tokens,
               'finalized_from_disk': {'reason': 'summarize() TypeError after all calls/oracles completed',
                                       'finalize_driver_sha256': P.h(Path(__file__).read_bytes())}}
    P.base.write(root / 'receipt.json', receipt)
    print(json.dumps({'protocol_valid': receipt['protocol_valid'], 'calls': calls, 'tokens': tokens,
                      'summary': rows['_summary']}, indent=1))
    for k, r in sorted(rows.items()):
        if k != '_summary':
            print('%-26s repaired %d/%d  @1 %d  harmful %d  rounds %s' % (k, r['repaired'], r['n'], r['at1'], r['harmful'], r['rounds']))
    return 0


def audit():
    rec = json.loads((ROOT / 'receipt.json').read_text())
    all_tasks = tasks()
    oracle_mod = P.load_oracle()
    checks = {'tasks_pinned': {t: P.h(s) for t, s in all_tasks.items()} == rec['lock']['tasks'],
              'symptoms_rederived': {t: B.classify_obs((ROOT / 'observer' / t / 'sim.log').read_text())['class']
                                     for t in sorted(all_tasks)} == rec['lock']['symptoms'],
              'bundles_unchanged_now': all(P.base.fsha(P.base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') ==
                                           rec['lock']['sqlite_before'][v] for v in P.base.BUNDLES)}
    for key, r in rec['results'].items():
        tid, arm, s = key.split('/')
        current = all_tasks[tid]
        for x in r['rounds']:
            rnd = x['round']
            d = ROOT / 'calls' / tid / arm / s
            msgs = json.loads((d / ('r%d.messages.json' % rnd)).read_text())
            checks['%s/r%d:hygiene' % (key, rnd)] = not any(w in json.dumps(msgs) for w in P.FORBIDDEN_IN_REQUEST)
            if x['kind'] in ('SKIPPED_FOR_CAP',):
                continue
            req = json.loads((d / ('r%d.request.json' % rnd)).read_bytes())
            checks['%s/r%d:request_is_messages' % (key, rnd)] = req['messages'] == msgs
            try:
                content = json.loads((d / ('r%d.response.json' % rnd)).read_bytes())['choices'][0]['message']['content']
                cand, _ = P.apply_proposal(content, current)
            except Exception:
                cand = None
            checks['%s/r%d:candidate' % (key, rnd)] = (P.h(cand) if cand is not None else None) == x['candidate_sha256']
            if cand is None:
                break
            od = ROOT / 'oracles' / tid / arm / s / ('r%d' % rnd)
            res = json.loads((od / 'result.json').read_text())
            stdout = (od / 'sim.stdout.log').read_text(errors='replace') if (od / 'sim.stdout.log').exists() else ''
            derived = oracle_mod.verdicts(res['sim'], stdout) if res['sim'] else {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}
            duts = sorted(od.glob('dut.*'))
            checks['%s/r%d:oracle' % (key, rnd)] = len(duts) == 1 and duts[0].read_bytes() == cand.encode() and derived == x['verdicts']
            if 'next_symptom' in x:
                ob = ROOT / 'observer-rounds' / tid / arm / s / ('r%d' % rnd) / 'sim.log'
                checks['%s/r%d:observation' % (key, rnd)] = B.classify_obs(ob.read_text() if ob.exists() else '')['class'] == x['next_symptom']
            current = cand
    out = {'schema': 'r5-phase3b-part2-cold-audit-v1', 'valid': all(checks.values()), 'check_count': len(checks),
           'failed': sorted(k for k, v in checks.items() if not v), 'checks': checks}
    print(json.dumps({k: v for k, v in out.items() if k != 'checks'}, indent=1))
    return out


def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == 'dry-run':
        return dry_run(Path(a[1]).resolve() / 'p3b2-dry')
    if len(a) == 5 and a[0] == 'run' and a[1] == '--gate' and a[3] == '--credentials':
        return run(a[2], a[4])
    if a == ['finalize']:
        return finalize()
    if a == ['audit']:
        out = audit()
        P.base.write(ROOT / 'cold-audit.json', out)
        return 0 if out['valid'] else 1
    raise SystemExit(__doc__)


if __name__ == '__main__':
    raise SystemExit(main())
