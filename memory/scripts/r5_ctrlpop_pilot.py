"""Frozen-v3 four-arm pilot on an admitted controlled-population target (Phase 2 card b5183ee: design 7;
Phase 2b card: design 25, same driver shape).

usage:
  r5_ctrlpop_pilot.py run [7|25]                   registered run (evidence root must not exist; default 7)
  r5_ctrlpop_pilot.py audit [7|25]                 separate-process cold audit of the registered run
  r5_ctrlpop_pilot.py --plumbing-train SCRATCH     plumbing check on the TRAIN alex source, no oracle
Routing/binding/action code comes from the frozen v3-M0 worktree (0128377); no model calls.
"""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r5_i2c_v3_consumer_checks as base  # noqa: E402  (imports frozen tehm from the v3-M0 worktree 0128377)
from contracts import MemoryQuery  # noqa: E402
from tehm.assets import r5_train_evidence_i2c_v3 as evidence  # noqa: E402
from tehm.assets import r5_train_raw_i2c_v3 as raw  # noqa: E402
from tehm.evaluation import research_r5_train_m0_i2c_v3 as m0  # noqa: E402
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets  # noqa: E402
from tehm.retrieval.memory_router import route_memory  # noqa: E402
from tehm.rtl import i2c_nack_action_v3 as action  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402

PILOT = base.PILOT
CTRL = PILOT / 'controlled/i2c-nack-pop-v1'
TARGETS = {  # design -> (card commit, fault source, fault sha, clean source, clean sha)
    7: ('b5183ee', CTRL / 'admit/run-r2/d07/fault.v', '612456bf5bdf2e78d080541e6593b3b7f84537f5aee88538852e37f9114ab87e',
        CTRL / 'generate/live-main-r1/design-07.v', 'daa7836a02d384c3865cba5dee9d9fb0abbd5f057186327737e48a1b3a124b9d'),
    25: ('4a8e64d', CTRL / 'admit/run-tf-r1/admission/d25/fault.v', 'd3242b2efe38e7e96e7b23110520a41df94a9c23450f4ffc891ce3ddcf2402b4',
         CTRL / 'generate/live-tf-r1/design-25.v', 'fd606f883df5c431ed5d9e73f0bb9100f03611e5c47d1bec6bb554ef73872b2d'),
}
DESIGN = int(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] in ('run', 'audit') else 7
CARD, FAULT_SRC, FAULT_SHA, CLEAN_SRC, CLEAN_SHA = TARGETS[DESIGN]
ROOT = PILOT / ('transfer/ctrlpop-%03d-pilot-r1' % DESIGN)
ORACLE_TB_SHA = 'ee67a1b38125416876f042d19c22f16558373590f976eb32833f6ea724989690'
ORACLE_RUN_SHA = '71eed56c20aa67342c3995e3c6016c51f6351b1c5126fe88e9fad82c285e2f2d'
CONSUMER_SHA = '8dffb0e19a8088fd0056901a572de487a5502a1e94976002d49ee3a562bccd91'
CONTEXT = {'interface': 'dedicated_nack_status_register_output', 'status_output': 'nack', 'defined_macros': []}
TASKS = ('ctrlpop_%03d_fault' % DESIGN, 'ctrlpop_%03d_healthy' % DESIGN)
OSS = Path('/opt/pdk_klayout_openroad/oss-cad-suite/bin')


def h(data):
    return hashlib.sha256(data).hexdigest()


def target_sources():
    fault, clean = FAULT_SRC.read_bytes(), CLEAN_SRC.read_bytes()
    assert h(fault) == FAULT_SHA and h(clean) == CLEAN_SHA
    return None, {TASKS[0]: fault.decode(), TASKS[1]: clean.decode()}


def load_runner(_freeze=None):
    path = CTRL / 'oracle/run.py'
    assert h(path.read_bytes()) == ORACLE_RUN_SHA and h((CTRL / 'oracle/tb_spec_nack.sv').read_bytes()) == ORACLE_TB_SHA
    spec = importlib.util.spec_from_file_location('ctrlpop_oracle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def oracle(runner, candidate_text, out):
    """Frozen controlled-population oracle on one candidate (its own evaluate(): same commands and adapter)."""
    r = runner.evaluate(candidate_text, out)
    return {'compile': r['compile'], 'sim': r['sim'], 'verdicts': r['verdicts'],
            'candidate_sha256': h(candidate_text.encode()), 'info': r['info']}


def run(root, sources, context, with_oracle):
    report = json.loads((base.MEMORY / 'research/m0-build-report.json').read_text())
    assert report['report_digest'] == base.REPORT
    assert base.git(base.FROZEN, 'rev-parse', 'HEAD') == base.FROZEN_HEAD and not base.git(base.FROZEN, 'status', '--porcelain')
    assert h(base.CONSUMER.read_bytes()) == CONSUMER_SHA
    root.mkdir(exist_ok=False)
    cold = m0.verify(base.MEMORY)
    base.write(root / 'cold-memory-verify.json', cold)
    assert cold['valid'] and cold['report_digest'] == base.REPORT
    before = {v: base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in base.BUNDLES}
    lock = {'schema': 'r5-ctrlpop-pilot-lock-v1', 'design': DESIGN, 'contract_commit': CARD,
            'frozen_software_head': base.FROZEN_HEAD, 'main_repo_head': base.git(base.REPO, 'rev-parse', 'HEAD'),
            'main_repo_clean': not base.git(base.REPO, 'status', '--porcelain', '--untracked-files=all'),
            'memory_report_digest': base.REPORT, 'bundle_digests': cold['bundle_digests'], 'sqlite_before': before,
            'consumer_sha256': CONSUMER_SHA, 'driver_sha256': base.fsha(__file__),
            'oracle_tb_sha256': ORACLE_TB_SHA, 'oracle_runner_sha256': ORACLE_RUN_SHA, 'public_context': context,
            'sources': {t: 'sha256:' + h(s.encode()) for t, s in sources.items()}}
    base.write(root / 'pre_execution_lock.json', lock)
    acquisitions = json.loads((base.MEMORY / 'research/parent-acquisitions.json').read_text())
    plan = report['train_consumption_preflight']['query_plan']
    conns = {v: base.reload(v) for v in base.BUNDLES}
    arms = {}
    try:
        for task, text in sources.items():
            task_dir = root / 'tasks' / task
            (task_dir / 'inputs').mkdir(parents=True)
            (task_dir / 'inputs/source.txt').write_text(text)
            base.write(task_dir / 'inputs/public_context.json', context)
            ctx = json.loads((task_dir / 'inputs/public_context.json').read_text())
            query = MemoryQuery(query_plan={**plan, 'design_id': task})
            ids = {'source_sha256': base.fsha(task_dir / 'inputs/source.txt'),
                   'context_sha256': base.fsha(task_dir / 'inputs/public_context.json')}
            for view in base.VIEWS:
                handoff = {'schema': base.SCHEMA, 'view': view, **ids, 'route': 'BYPASS_MEMORY',
                           'selection': 'DIRECT_PRIMITIVE', 'selected_asset_ids': [], 'action_payload': None}
                authority = None
                if view in base.BUNDLES:
                    conn = conns[view]
                    replay = (scoped_learning_replay(conn, campaign_id=acquisitions['campaign_id'],
                                                     acquisitions=acquisitions['acquisitions'],
                                                     expected_digest=acquisitions['digest'])
                              if view == 'm-plus' else nullcontext())
                    with replay:
                        route = route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                                             persist_state=False, commit=False)
                        selection = select_knowledge_grounded_assets(
                            conn, query, routing=route, rtl_source_text=text, design_id=task,
                            rtl_public_context=ctx)
                    assert len(selection.assets) <= 1
                    authority = {'route': route.to_dict(), 'selection': selection.receipt.to_dict()}
                    base.write(task_dir / ('authority-' + view + '.json'), authority)
                    handoff.update(route=route.decision, selection=selection.receipt.decision,
                                   selected_asset_ids=list(selection.receipt.selected_asset_ids),
                                   action_payload=(selection.assets[0]['definition']['action']['payload']
                                                   if selection.assets else None))
                arms[(task, view)] = {'handoff': handoff, 'authority': authority,
                                      **base.consume(task_dir, handoff, view)}
    finally:
        for conn in conns.values():
            conn.close()
    emitted = {f'{t}/{v}': a['candidate'] is not None for (t, v), a in arms.items()}
    base.write(root / 'candidates-emitted.json', emitted)  # written before any oracle execution
    oracles = {}
    if with_oracle:
        runner = load_runner()
        for (task, view), arm in arms.items():
            if arm['candidate'] is not None:
                oracles[f'{task}/{view}'] = oracle(runner, arm['candidate'], root / 'oracles' / task / view)
    after = {v: base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in base.BUNDLES}
    invariants = {
        'I1_bundles_unchanged': before == after,
        'I1_frozen_worktree_clean': (base.git(base.FROZEN, 'rev-parse', 'HEAD') == base.FROZEN_HEAD
                                     and not base.git(base.FROZEN, 'status', '--porcelain')),
        'I1_main_repo_clean_at_start': lock['main_repo_clean'],
        'I2_mremove_equals_m_minus': all(arms[(t, 'mremove')]['authority'] == arms[(t, 'm-minus')]['authority']
                                         for t in sources),
        'I4_all_candidates_before_oracle': all(emitted.values()),
        'I5_consumer_pinned': h(base.CONSUMER.read_bytes()) == CONSUMER_SHA,
    }
    for (task, view), arm in arms.items():
        if arm['receipt'] and arm['receipt']['action'] == 'APPLIED':
            expected, _ = apply_rtl_action(sources[task], action.payload_from_source_i2c_v3(sources[task], context))
            invariants[f'I3_applied_equals_frozen_action_{task}_{view}'] = arm['candidate'] == expected
    rows = {}
    for (task, view), arm in arms.items():
        r = arm['receipt'] or {}
        o = oracles.get(f'{task}/{view}', {})
        v = o.get('verdicts', {})
        rows[f'{task}/{view}'] = {
            'route': arm['handoff']['route'], 'selection': arm['handoff']['selection'],
            'selected_asset_ids': arm['handoff']['selected_asset_ids'],
            'binding': r.get('binding'), 'action': r.get('action'), 'source_changed': r.get('source_changed'),
            'consumer_rc': arm['process']['returncode'], 'candidate_sha256': r.get('candidate_sha256'),
            'selection_reasons': (arm['authority'] or {}).get('selection', {}).get('abstain_reasons'),
            'target': v.get('target'), 'preservation': v.get('preservation'),
            'verified_repair': task.endswith('_fault') and v.get('target') == 'PASS' and v.get('preservation') == 'PASS',
            'healthy_false_action': task.endswith('_healthy') and bool(r.get('source_changed'))}
    receipt = {'schema': 'r5-ctrlpop-pilot-v1', 'design': DESIGN, 'role': 'PILOT_TRANSFER_CONTROLLED_POPULATION', 'lock': lock,
               'protocol_valid': all(invariants.values()), 'invariants': invariants, 'rows': rows,
               'oracles': oracles, 'sqlite_after': after, 'consumer_runs': len(arms),
               'oracle_runs': len(oracles), 'model_calls': 0, 'native_oracle': False,
               'oracle_label': 'spec_conformance_oracle_not_native', 'repair_task_denominator': 1,
               'healthy_control_denominator': 1, 'independent_authorship_proven': False,
               'population_lineage': 'generator deepseek-v4-pro (spec-only prompt)'}
    base.write(root / 'receipt.json', receipt)
    print(json.dumps({'protocol_valid': receipt['protocol_valid'],
                      'failed_invariants': [k for k, v in invariants.items() if not v]}, indent=1))
    for key, row in rows.items():
        print(key, {k: row[k] for k in ('route', 'selection', 'binding', 'action', 'target', 'preservation',
                                        'verified_repair', 'healthy_false_action')})
    return 0 if receipt['protocol_valid'] else 1


def audit():
    """Separate-process cold audit: re-derive verdicts and invariants from raw files only."""
    import re
    rec = json.loads((ROOT / 'receipt.json').read_text())
    checks = {'tb_pin': h((CTRL / 'oracle/tb_spec_nack.sv').read_bytes()) == ORACLE_TB_SHA,
              'runner_pin': h((CTRL / 'oracle/run.py').read_bytes()) == ORACLE_RUN_SHA,
              'bundles_unchanged_now': all(base.fsha(base.MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') ==
                                           rec['lock']['sqlite_before'][v] for v in base.BUNDLES),
              'protocol_valid_recorded': rec['protocol_valid'] is True}
    for task in TASKS:
        src = (ROOT / 'tasks' / task / 'inputs/source.txt').read_bytes()
        want = FAULT_SHA if task.endswith('_fault') else CLEAN_SHA
        checks[f'{task}_source_pin'] = h(src) == want
        m = json.loads((ROOT / 'tasks' / task / 'authority-m-minus.json').read_text())
        r = json.loads((ROOT / 'tasks' / task / 'authority-mremove.json').read_text())
        checks[f'{task}_mremove_equals_m_minus'] = m == r
        for view in base.VIEWS:
            key = f'{task}/{view}'
            cand = ROOT / 'tasks' / task / 'out' / view / 'candidate.txt'
            od = ROOT / 'oracles' / task / view
            duts = sorted(od.glob('dut.*'))
            checks[f'{key}_oracle_input_is_candidate'] = len(duts) == 1 and duts[0].read_bytes() == cand.read_bytes()
            checks[f'{key}_oracle_tb_pinned'] = h((od / 'tb_spec_nack.sv').read_bytes()) == ORACLE_TB_SHA
            so, se = od / 'sim.stdout.log', od / 'sim.stderr.log'
            lines = so.read_text(errors='replace').splitlines() if so.exists() else []
            err = se.read_bytes() if se.exists() else b''
            sim = rec['oracles'][key]['sim'] or {}
            ok = (sim.get('rc') == 0 and not sim.get('timed_out') and so.exists() and not err and lines.count('AUG_DONE') == 1 and
                  not any(l.startswith('AUG_TIMEOUT') for l in lines))
            derived = {}
            for scope in ('TARGET', 'PRESERVATION'):
                hit = [l for l in lines if re.fullmatch('AUG_%s (PASS|FAIL)' % scope, l)]
                derived[scope.lower()] = hit[0][-4:] if ok and len(hit) == 1 else 'UNKNOWN'
            row = rec['rows'][key]
            checks[f'{key}_verdicts_rederived'] = derived == {'target': row['target'], 'preservation': row['preservation']}
            checks[f'{key}_source_changed_rederived'] = (cand.read_bytes() != src) == bool(row['source_changed'])
    out = {'schema': 'r5-ctrlpop-pilot-cold-audit-v1', 'design': DESIGN, 'valid': all(checks.values()),
           'check_count': len(checks), 'failed': sorted(k for k, v in checks.items() if not v), 'checks': checks}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out['valid'] else 1


def main():
    if len(sys.argv) in (2, 3) and sys.argv[1] == 'run':
        _, sources = target_sources()
        return run(ROOT, sources, CONTEXT, with_oracle=True)
    if len(sys.argv) in (2, 3) and sys.argv[1] == 'audit':
        return audit()
    if len(sys.argv) == 3 and sys.argv[1] == '--plumbing-train':
        root = Path(sys.argv[2]).resolve() / 'ctrlpop-pilot-plumbing-train'
        assert not root.is_relative_to(PILOT)
        alex = {'train_alex_fault': evidence.train_source('alex')}
        return run(root, alex, raw.SOURCES['alex']['public_context'], with_oracle=False)
    raise SystemExit(__doc__)


if __name__ == '__main__':
    raise SystemExit(main())
