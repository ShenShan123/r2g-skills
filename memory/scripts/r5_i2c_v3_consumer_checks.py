"""TRAIN-only conformance of the I2C v3 four-view consumer and transform-only arm.

Contract: memory/evaluation/research_r5_i2c_nack_v3_train_generation_contract_20260929.md (2f).
Every routing/selection/binding import comes from the frozen v3-M0 worktree. No
simulator, model call, Memory write or target task. Evidence root must not exist.
"""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

FROZEN = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/software/frozen-i2c-v3m0-0128377')
FROZEN_HEAD = '01283774ed6a4694d2f19cec77cc90d9f700b0c9'
sys.path.insert(0, str(FROZEN / 'memory'))

from contracts import MemoryQuery  # noqa: E402
import tehm  # noqa: E402
from tehm.assets import r5_train_evidence_i2c_v3 as evidence  # noqa: E402
from tehm.assets import r5_train_raw_i2c_v3 as raw  # noqa: E402
from tehm.evaluation import research_r5_train_m0_i2c_v3 as m0  # noqa: E402
from tehm.evaluation.research_r5_s2_native import sandbox, execute  # noqa: E402
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets  # noqa: E402
from tehm.retrieval.memory_router import route_memory  # noqa: E402
from tehm.rtl import i2c_nack_action_v3 as action  # noqa: E402
from tehm.rtl import i2c_nack_action_v2 as transport  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.sync import verify_bundle  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PILOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')
MEMORY = PILOT / 'memory/r5-i2c-v3-m0-r1'
REPORT = 'sha256:6b76d3998d9e54adac0d596c70d26f60de0b6f23edc5c2acf2702b3b9b74e8b4'
ROOT = PILOT / 'transfer/i2c-v3-consumer-conformance-r1'
CONSUMER = Path(__file__).with_name('r5_i2c_v3_consumer.py')
BUNDLES = ('m-minus', 'm-plus', 'mremove')
VIEWS = (*BUNDLES, 'transform-only')
SCHEMA = 'r5-i2c-v3-source-only-handoff-v1'
CLEAN = {'chance189': 'original/oracles/chance189/candidate/stage/i2c_master.v',
         'alex': 'original/oracles/alex/candidate/stage/rtl/i2c_master.v',
         'zip': 'original/oracles/zip/candidate/stage/rtl/wbi2cmaster.v',
         'freecores': 'original/oracles/freecores/candidate/stage/rtl/verilog/i2c_master_top.v'}


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def fsha(path):
    return sha(Path(path).read_bytes())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def sources():
    out = {}
    for case in evidence.CASE_ORDER:
        fault = evidence.train_source(case)
        clean_file = (raw.TRAIN / CLEAN[case]).read_text()
        if case == 'freecores':
            roles = transport.decode_closure(fault)
            clean = transport.encode_closure({**roles, 'top': clean_file})
            hashes = {r: sha(t.encode()) for r, t in transport.decode_closure(clean).items()}
        else:
            clean, hashes = clean_file, sha(clean_file.encode())
        assert hashes == raw.SOURCES[case]['candidate_sha256'], 'clean counterpart drift ' + case
        out[case + '_fault'] = (case, fault)
        out[case + '_clean'] = (case, clean)
    return out


def reload(view):
    src = sqlite3.connect('file:' + str(MEMORY / 'bundles' / view / 'closed_loop/tehm.sqlite') + '?mode=ro',
                          uri=True)
    ram = sqlite3.connect(':memory:')
    ram.row_factory = sqlite3.Row
    try:
        src.backup(ram)
    finally:
        src.close()
    return ram


def consume(task_dir, handoff, name, extra_input=None):
    """Stage inputs and run the consumer once in bwrap; returns the process record."""
    inputs = task_dir / 'inputs'
    handoff_path = task_dir / ('handoff-' + name + '.json')
    write(handoff_path, handoff)
    out = task_dir / 'out' / name
    out.mkdir(parents=True)
    staged = inputs
    if extra_input is not None:
        staged = task_dir / ('inputs-' + name)
        staged.mkdir()
        for p in inputs.iterdir():
            (staged / p.name).write_bytes(p.read_bytes())
        (staged / extra_input).write_text('leak\n')
    argv = sandbox() + ['--ro-bind', str(FROZEN / 'memory'), '/app',
                        '--ro-bind', str(staged), '/inputs',
                        '--ro-bind', str(handoff_path), '/handoff.json',
                        '--ro-bind', str(CONSUMER), '/consumer.py',
                        '--bind', str(out), '/out', '--chdir', '/',
                        '/usr/bin/python3', '-I', '-B', '/consumer.py']
    (task_dir / 'process').mkdir(exist_ok=True)
    process = execute(argv, task_dir / 'process' / name, 120)
    receipt = json.loads((out / 'consumer.json').read_text()) if (out / 'consumer.json').exists() else None
    return {'process': process, 'receipt': receipt,
            'candidate_written': (out / 'candidate.txt').exists(),
            'candidate': (out / 'candidate.txt').read_text() if (out / 'candidate.txt').exists() else None}


def main():
    global ROOT
    dry = len(sys.argv) == 3 and sys.argv[1] == '--dry-run'
    assert len(sys.argv) == 1 or dry, 'usage: [--dry-run SCRATCH_DIR]'
    if dry:  # development only: never the registered evidence root
        ROOT = Path(sys.argv[2]).resolve() / 'i2c-v3-consumer-conformance-dry'
        assert not ROOT.is_relative_to(PILOT)
    assert Path(tehm.__file__).resolve().is_relative_to(FROZEN)
    assert git(FROZEN, 'rev-parse', 'HEAD') == FROZEN_HEAD and not git(FROZEN, 'status', '--porcelain')
    assert dry or not git(REPO, 'status', '--porcelain', '--untracked-files=all'), 'main tree must be clean'
    report = json.loads((MEMORY / 'research/m0-build-report.json').read_text())
    assert report['report_digest'] == REPORT
    ROOT.mkdir(exist_ok=False)
    cold = m0.verify(MEMORY)
    write(ROOT / 'cold-memory-verify.json', cold)
    assert cold['valid'] and cold['report_digest'] == REPORT
    before = {v: fsha(MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in BUNDLES}
    for v in BUNDLES:
        checked = verify_bundle(MEMORY / 'bundles' / v)
        assert checked['ok'] and checked['manifest']['bundle_digest'] == cold['bundle_digests'][v]
    srcs = sources()
    lock = {'schema': 'r5-i2c-v3-consumer-conformance-lock-v1', 'frozen_software_head': FROZEN_HEAD,
            'main_repo_head': git(REPO, 'rev-parse', 'HEAD'), 'memory_report_digest': REPORT,
            'bundle_digests': cold['bundle_digests'], 'sqlite_before': before,
            'consumer_sha256': fsha(CONSUMER), 'driver_sha256': fsha(__file__),
            'train_receipt_sha256': raw.RECEIPT_SHA,
            'sources': {k: sha(t.encode()) for k, (_, t) in srcs.items()},
            'contexts': {c: raw.SOURCES[c]['public_context'] for c in evidence.CASE_ORDER}}
    write(ROOT / 'pre_execution_lock.json', lock)
    acquisitions = json.loads((MEMORY / 'research/parent-acquisitions.json').read_text())
    base_plan = report['train_consumption_preflight']['query_plan']
    conns = {v: reload(v) for v in BUNDLES}
    arms = {}
    try:
        for task, (case, text) in srcs.items():
            task_dir = ROOT / 'tasks' / task
            (task_dir / 'inputs').mkdir(parents=True)
            (task_dir / 'inputs/source.txt').write_text(text)
            write(task_dir / 'inputs/public_context.json', raw.SOURCES[case]['public_context'])
            context = json.loads((task_dir / 'inputs/public_context.json').read_text())
            design_id = task + '_consumer_conformance'
            query = MemoryQuery(query_plan={**base_plan, 'design_id': design_id})
            ids = {'source_sha256': fsha(task_dir / 'inputs/source.txt'),
                   'context_sha256': fsha(task_dir / 'inputs/public_context.json')}
            for view in VIEWS:
                handoff = {'schema': SCHEMA, 'view': view, **ids, 'route': 'BYPASS_MEMORY',
                           'selection': 'DIRECT_PRIMITIVE', 'selected_asset_ids': [],
                           'action_payload': None}
                authority = None
                if view in BUNDLES:
                    conn = conns[view]
                    replay = (scoped_learning_replay(conn, campaign_id=acquisitions['campaign_id'],
                                                     acquisitions=acquisitions['acquisitions'],
                                                     expected_digest=acquisitions['digest'])
                              if view == 'm-plus' else nullcontext())
                    with replay:
                        route = route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                                             persist_state=False, commit=False)
                        selection = select_knowledge_grounded_assets(
                            conn, query, routing=route, rtl_source_text=text, design_id=design_id,
                            rtl_public_context=context)
                    assert len(selection.assets) <= 1
                    authority = {'route': route.to_dict(), 'selection': selection.receipt.to_dict()}
                    write(task_dir / ('authority-' + view + '.json'), authority)
                    handoff.update(route=route.decision, selection=selection.receipt.decision,
                                   selected_asset_ids=list(selection.receipt.selected_asset_ids),
                                   action_payload=(selection.assets[0]['definition']['action']['payload']
                                                   if selection.assets else None))
                arms[(task, view)] = {'handoff': handoff, 'authority': authority,
                                      **consume(task_dir, handoff, view)}
    finally:
        for conn in conns.values():
            conn.close()

    # Consumer negatives on the alex fault source; each must exit nonzero with no candidate.
    neg_dir = ROOT / 'negatives'
    (neg_dir / 'inputs').mkdir(parents=True)
    _, alex = srcs['alex_fault']
    (neg_dir / 'inputs/source.txt').write_text(alex)
    write(neg_dir / 'inputs/public_context.json', raw.SOURCES['alex']['public_context'])
    good = {**arms[('alex_fault', 'transform-only')]['handoff']}
    payload = action.payload_from_source_i2c_v3(alex, raw.SOURCES['alex']['public_context'])
    select = {**good, 'view': 'm-plus', 'route': 'CONSIDER', 'selection': 'SELECT',
              'selected_asset_ids': ['asset_x'], 'action_payload': payload}
    negatives = {
        'source_sha_mismatch': ({**good, 'source_sha256': 'sha256:' + '0' * 64}, None),
        'context_sha_mismatch': ({**good, 'context_sha256': 'sha256:' + '0' * 64}, None),
        'transform_only_with_payload': ({**good, 'action_payload': payload}, None),
        'select_without_payload': ({**select, 'action_payload': None}, None),
        'stale_payload': ({**select, 'action_payload': {**payload, 'source_sha256': 'sha256:' + '1' * 64}}, None),
        'extra_input_file': (good, 'clean_hint.v'),
        'unknown_view': ({**good, 'view': 'oracle'}, None),
    }
    neg = {name: consume(neg_dir, h, name, extra) for name, (h, extra) in negatives.items()}

    after = {v: fsha(MEMORY / 'bundles' / v / 'closed_loop/tehm.sqlite') for v in BUNDLES}
    frozen_clean = (git(FROZEN, 'rev-parse', 'HEAD') == FROZEN_HEAD and not git(FROZEN, 'status', '--porcelain'))

    def ok(arm):
        return arm['process']['returncode'] == 0 and arm['receipt'] is not None

    checks = {}
    for task, (case, text) in srcs.items():
        t = arms[(task, 'transform-only')]
        if task.endswith('_fault'):
            want = raw.SOURCES[case]['candidate_sha256']
            got = (sha(t['candidate'].encode()) if case != 'freecores' else
                   {r: sha(x.encode()) for r, x in transport.decode_closure(t['candidate']).items()})
            checks['R1_transform_only_repairs_' + task] = (
                ok(t) and t['receipt']['binding']['status'] == 'BOUND' and t['receipt']['action'] == 'APPLIED'
                and t['receipt']['edit'].get('rewritten_spans') == 1 and got == want)
        else:
            checks['R2_transform_only_abstains_' + task] = (
                ok(t) and t['receipt']['binding']['status'] == 'NO_MATCH' and t['receipt']['action'] == 'NO_ACTION'
                and t['candidate'] == text)
        mm, mr = arms[(task, 'm-minus')], arms[(task, 'mremove')]
        checks['R3_no_memory_views_inert_' + task] = (
            ok(mm) and ok(mr) and all(a['handoff']['selection'] != 'SELECT' and a['receipt']['action'] == 'NO_ACTION'
                                      and a['candidate'] == text for a in (mm, mr))
            and mm['authority'] == mr['authority'])
        for view in VIEWS:
            arm = arms[(task, view)]
            if ok(arm) and arm['receipt']['action'] == 'APPLIED':
                expected, _ = apply_rtl_action(text, action.payload_from_source_i2c_v3(
                    text, raw.SOURCES[case]['public_context']))
                checks['R4_applied_equals_frozen_action_%s_%s' % (task, view)] = arm['candidate'] == expected
    checks['R5_memory_bundles_unchanged'] = before == after
    checks['R5_frozen_worktree_clean'] = frozen_clean
    for name, result in neg.items():
        checks['R6_negative_' + name] = result['process']['returncode'] != 0 and not result['candidate_written']
    observed = {task: {view: {'route': arms[(task, view)]['handoff']['route'],
                              'selection': arms[(task, view)]['handoff']['selection'],
                              'action': (arms[(task, view)]['receipt'] or {}).get('action'),
                              'binding': (arms[(task, view)]['receipt'] or {}).get('binding'),
                              'source_changed': (arms[(task, view)]['receipt'] or {}).get('source_changed'),
                              'candidate_sha256': (arms[(task, view)]['receipt'] or {}).get('candidate_sha256')}
                       for view in VIEWS} for task in srcs}
    false_actions = sorted('%s/%s' % (task, view) for task in srcs if task.endswith('_clean')
                           for view in VIEWS if observed[task][view]['source_changed'])
    receipt = {'schema': 'r5-i2c-v3-consumer-conformance-v1', 'role': 'TRAIN_ONLY_CONSUMER_CONFORMANCE',
               'dry_run': dry,
               'valid': all(checks.values()), 'check_count': len(checks),
               'failed': sorted(k for k, v in checks.items() if not v), 'checks': checks,
               'observed': observed, 'healthy_false_actions': false_actions,
               'negatives': {k: v['process'] for k, v in neg.items()}, 'lock': lock,
               'sqlite_after': after, 'consumer_runs': len(arms), 'negative_runs': len(neg),
               'model_calls': 0, 'new_simulator_executions': 0, 'heldout_transfer': False,
               'delta_memory_claim': False, 'TRAIN_ONLY': True}
    write(ROOT / 'receipt.json', receipt)
    print(json.dumps({k: receipt[k] for k in ('valid', 'check_count', 'failed', 'healthy_false_actions',
                                              'consumer_runs', 'negative_runs')}, indent=2))
    print(json.dumps({t: {v: (o['selection'], o['action']) for v, o in vs.items()}
                      for t, vs in observed.items()}, indent=1))
    return 0 if receipt['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
