#!/usr/bin/env python3
"""Frozen, no-LLM/no-repair qualification for the first Table 2 source families.

prepare creates a new private snapshot, registered inputs and cost/safety budget.
run handles one family in default/u80/u90 order, stopping at two verified failures.
No retries, no replacement designs, no automatic formal-arm launch.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
SOURCE = Path('/data1/zhangdy/RTL/test/table2-orfs-eb14d768')
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
SOURCE_COMMIT = 'eb14d768b6c34cf4f8c5177f3531422b94cf2544'
DESIGNS = {'ibex': ('ibex_core', 'ibex_sv', 50), 'riscv32i': ('riscv', 'riscv32i', 45),
           'gcd': ('gcd', 'gcd', 40)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare(root, designs=None):
    assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == SOURCE_COMMIT
    # A sparse partial clone's `git status` can lazily fetch unrelated objects.
    # Validate every available corpus file directly against the pinned index instead.
    entries = {}
    for row in subprocess.check_output(['git', '-C', str(SOURCE), 'ls-files', '--stage', '-z']).split(b'\0'):
        if row:
            meta, name = row.split(b'\t', 1)
            entries[name.decode()] = meta.split()[1].decode()
    for path in (SOURCE / 'flow/designs').rglob('*'):
        if path.is_file():
            data = path.read_bytes()
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            assert entries.get(str(path.relative_to(SOURCE))) == blob, path
    root.mkdir(parents=True, exist_ok=False)
    snapshot = root / 'code'
    for rel in ('r2g-skills/signoff-loop', 'r2g-skills/def-graph', 'memory/scripts', 'tools'):
        shutil.copytree(REPO / rel, snapshot / rel, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
    pin = Path('r2g-skills/signoff-loop/references/env.local.sh')
    shutil.copy2(EVAL / 'toolchain/frozen-r2g-cc6d103' / pin, snapshot / pin)
    inputs = root / 'source'
    shutil.copytree(SOURCE / 'flow/designs', inputs)
    setup = module('t2a_project_setup', snapshot / 'tools/setup_rtl_designs.py')
    tasks = []
    for design, (top, src, initial_util) in DESIGNS.items():
        if designs and design not in designs:
            continue
        cfgdir = inputs / 'sky130hd' / design
        # Preserve shipped design settings/SDC; use the existing generator for shared R2G hooks/defaults.
        source_cfg = (cfgdir / 'config.mk').read_text().replace('\\\n', ' ')
        for variant, util in (('default', initial_util), ('u80', 80), ('u90', 90)):
            project = root / 'projects' / (design + '__' + variant)
            for sub in setup.TEMPLATE_DIRS:
                (project / sub).mkdir(parents=True, exist_ok=False)
            shutil.copytree(inputs / 'src' / src, project / 'rtl', dirs_exist_ok=True)
            suffix = '*.sv' if design == 'ibex' else '*.v'
            rtl = sorted((project / 'rtl').glob(suffix))
            if design == 'ibex':
                rtl.append(project / 'rtl/syn/rtl/prim_clock_gating.v')
            assert rtl and all(f.is_file() for f in rtl)
            sdc = project / 'constraints/constraint.sdc'
            shutil.copy2(cfgdir / 'constraint.sdc', sdc)
            clock = re.search(r'^\s*set\s+clk_period\s+([0-9.]+)', sdc.read_text(), re.M)
            if not clock:
                raise ValueError('Clock reference requires manual preregistration: ' + design)
            cfg, _, _ = setup.generate_config_mk(project, top, 'sky130hd', rtl, sdc, top_module=top)
            text = cfg.read_text().replace('\\\n', ' ')
            assignments = {}
            for line in source_cfg.splitlines():
                match = re.match(r'^\s*export\s+(\w+)\s*:?=\s*(.*?)\s*$', line)
                if match:
                    assignments[match[1]] = match[2]
            for key in ('DESIGN_NICKNAME', 'VERILOG_FILES', 'SDC_FILE', 'CORE_UTILIZATION'):
                assignments.pop(key, None)
            for key, value in assignments.items():
                value = value.replace('$(DESIGN_HOME)', str(inputs)).replace('$(PLATFORM)', 'sky130hd')
                value = value.replace('$(DESIGN_NICKNAME)', design)
                assert '$' not in value, (key, value)
                text = re.sub(r'^export\s+' + key + r'\s*[:?]?=.*\n', '', text, flags=re.M)
                text += 'export %s = %s\n' % (key, value)
            text = re.sub(r'^export CORE_UTILIZATION\s*=.*$', 'export CORE_UTILIZATION = %d' % util, text, flags=re.M)
            cfg.write_text(text)
            tasks.append({'design': design, 'variant': variant, 'project': str(project), 'util_variable': 'CORE_UTILIZATION',
                          'util_percent': util, 'stratum': 'natural' if variant == 'default' else 'stress',
                          'expected_clock_period_ns': float(clock[1]),
                          'config_sha256': digest(cfg), 'sdc_sha256': digest(sdc)})
    save(root / 'budget.json', {'budget_id': 't2a-qualification-a3',
         'caps': {'flows': 1, 'wall_s': 28800, 'llm_requests': 0, 'tokens_in': 0, 'tokens_out': 0},
         'reserve': {'wall_s': 1800}})
    immutable = [p for base in (snapshot, inputs) for p in base.rglob('*') if p.is_file()]
    immutable += [root / 'budget.json']
    immutable += [Path(t['project']) / 'constraints' / name for t in tasks for name in ('config.mk', 'constraint.sdc')]
    immutable += [p for t in tasks for p in (Path(t['project']) / 'rtl').rglob('*') if p.is_file()]
    yosys = Path('/data1/zhangdy/Tools/tehm-toolchain/yosys/bin/yosys')
    plugin = yosys.parent.parent / 'share/yosys/plugins/slang.so'
    save(root / 'registration.json', {'schema': 't2a-qualification-v1', 'registered_at_unix': time.time(),
         'source_commit': SOURCE_COMMIT, 'runtime_base_commit': subprocess.check_output(
             ['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
         'runtime_identity': 'file hashes include uncommitted A3 fixes; not base commit alone',
         'tasks': tasks, 'llm_requests_authorized': 0, 'order': ['default', 'u80', 'u90'],
         'stop_after_verified_failures_per_design': 2, 'no_repair': True, 'no_retry': True,
         'tool_files': {str(p): digest(p) for p in (yosys, plugin) if p.exists()},
         'files': {str(p.relative_to(root)): digest(p) for p in immutable}})
    print(json.dumps({'prepared': str(root), 'tasks_registered': len(tasks), 'registration_sha256': digest(root / 'registration.json')}))


def run(root, design):
    import fcntl
    lock = (root / (design + '.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    registration = json.loads((root / 'registration.json').read_text())
    for rel, expected in registration['files'].items():
        if digest(root / rel) != expected:
            raise RuntimeError('Frozen input changed: ' + rel)
    for path, expected in registration.get('tool_files', {}).items():
        if digest(path) != expected:
            raise RuntimeError('Registered tool changed: ' + path)
    H = module('t2a_qualification_harness', root / 'code/memory/scripts/r2g_memory_eval.py')
    H.SKILL = root / 'code/r2g-skills/signoff-loop'
    H.FLOW = H.SKILL / 'scripts/flow'
    H.CFG = {'platform': 'sky130hd'}
    H.CASES = root / 'projects'
    if not (root / 'code/r2g-skills/def-graph/scripts/flow/signoff_gate.py').is_file():
        raise RuntimeError('Incomplete runtime: strict signoff dependency missing')
    os.environ['R2G_BUDGET_FILE'] = str(root / 'budget.json')
    os.environ['R2G_REQUIRE_LLM_BUDGET'] = '1'
    env = H.base_env('none')
    env.update(R2G_BUDGET_COMPONENT='qualification', R2G_JOURNAL_DB=str(root / (design + '-journal.sqlite')),
               CUDA_VISIBLE_DEVICES='', PYTHONDONTWRITEBYTECODE='1')
    count, records = 0, []
    for task in [t for t in registration['tasks'] if t['design'] == design]:
        receipt = root / (design + '__' + task['variant'] + '.json')
        if receipt.exists():
            raise RuntimeError('Existing attempt: no implicit resume or retry')
        project = Path(task['project'])
        H._EPISODE['ledger'] = project
        rc, _ = H._ledger(project, 'event', '--type', 'attempt_start', '--json',
                          json.dumps({'phase': 'qualification', 'task': design + '/' + task['variant'],
                                      'budget_file': str(root / 'budget.json')}))
        if rc:
            raise RuntimeError('Ledger start failed')
        started = time.time()
        save(receipt, {**task, 'status': 'RUNNING', 'started': started, 'cpu_affinity': sorted(os.sched_getaffinity(0))})
        try:
            raw = H.control_flow(project, env)
            # Strict final checklist once, without repeating costly signoff.
            if raw['final_layout']:
                H.run(['python3', H.SKILL / 'scripts/reports/acceptance_check.py', project,
                       '--expected-clock-period', str(task['expected_clock_period_ns'])], env,
                      project / 'eval_flow.log', 300)
            acceptance = H.jload(project / 'reports/acceptance.json')
            ppa = H.jload(project / 'reports/ppa.json')
            stage = ppa.get('orfs_fail_stage') or (ppa.get('summary') or {}).get('orfs_fail_stage')
            status = 'REVIEW_REQUIRED'
            if raw['orfs_rc'] == 75:
                status = 'SAFETY_STOP'
            elif acceptance.get('accepted') is True:
                status = 'CLEAN'
            elif raw['orfs_rc'] != 0 and stage and stage != 'synth':
                status = 'FAILURE_CANDIDATE'  # raw evidence/infra audit still required before TEST admission
            elif raw['orfs_rc'] != 0:
                status = 'INFRASTRUCTURE_REVIEW'
            elif acceptance.get('unknown'):
                status = 'REVIEW_REQUIRED'
            elif acceptance.get('failed'):
                status = 'FAILURE_CANDIDATE'
            record = {**task, **raw, 'acceptance': acceptance, 'status': status, 'started': started,
                      'ended': time.time(), 'cpu_affinity': sorted(os.sched_getaffinity(0)),
                      'llm_requests': 0, 'paper_episode': False}
            save(receipt, record)
            records.append(record)
            print(json.dumps({'design': design, 'variant': task['variant'], 'status': status}), flush=True)
            # Stop conservatively at two failure candidates; do not search for more after review.
            if status == 'FAILURE_CANDIDATE':
                count += 1
            if count >= 2 or status in ('INFRASTRUCTURE_REVIEW', 'SAFETY_STOP', 'REVIEW_REQUIRED'):
                break
        except Exception as exc:
            save(receipt, {**task, 'status': 'INFRASTRUCTURE_ERROR', 'error': str(exc), 'started': started,
                           'ended': time.time(), 'paper_episode': False})
            raise
    save(root / (design + '-summary.json'), {'records': records, 'formal_test_manifest_frozen': False,
         'note': 'Every failure candidate requires infrastructure and lineage review; no automatic formal launch.'})


def recover(root, design):
    """Append a stopped-run audit from completed flow events; never retry a flow or rewrite a receipt."""
    target = root / (design + '-summary.json')
    if target.exists():
        raise RuntimeError('Summary already exists; no replacement')
    # Inspect in the host process namespace when recovering real jobs.
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().split(b'\0')
        except OSError:
            continue
        if (b'run' in args and str(root).encode() in args and
                any(a.endswith(b't2a_qualify.py') for a in args)):
            raise RuntimeError('Qualification process is still running')
    registration = json.loads((root / 'registration.json').read_text())
    records = []
    for task in registration['tasks']:
        if task['design'] != design:
            continue
        receipt = root / (design + '__' + task['variant'] + '.json')
        if not receipt.exists():
            continue
        raw = json.loads(receipt.read_text())
        if raw.get('status') != 'RUNNING':
            records.append(raw)
            continue
        ledger = Path(task['project']) / 'reports/resource_ledger.jsonl'
        events = [json.loads(line) for line in ledger.read_text().splitlines()]
        flows = [e for e in events if e.get('type') == 'flow_run']
        if len(flows) != 1:
            raise RuntimeError('Exactly one terminal flow outcome required for recovery')
        flow = flows[0]
        if flow.get('rc') == 0:
            raise RuntimeError('Successful flow requires its missing signoff checks, not failure recovery')
        record = {**raw, 'status': 'TIMEOUT_REVIEW' if flow.get('timeout') else 'INFRASTRUCTURE_REVIEW',
                  'original_receipt_sha256': digest(receipt), 'original_receipt': str(receipt),
                  'recovered_at': time.time(), 'ended': flow['ts'], 'orfs_rc': flow['rc'],
                  'final_layout': bool(list((Path(task['project']) / 'backend').rglob('6_final.*'))),
                  'wall_s': round(flow['ts'] - raw['started']), 'llm_requests': 0,
                  'ledger_sha256': digest(ledger), 'paper_episode': False,
                  'runner_exit_reason': 'UNKNOWN; runner did not write its terminal receipt'}
        audit = root / (design + '__' + task['variant'] + '.recovery.json')
        if audit.exists():
            raise RuntimeError('Recovery audit already exists')
        save(audit, record)
        records.append(record)
    save(target, {'records': records, 'formal_test_manifest_frozen': False,
                 'note': 'Stopped-run recovery preserves original receipts; no EDA or model calls.'})
    print(json.dumps({'summary': str(target), 'records': len(records), 'eda_flows_launched': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'recover'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--design', choices=DESIGNS)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.root.resolve(), [args.design] if args.design else None)
    else:
        if not args.design:
            parser.error('--design is required for run')
        (recover if args.action == 'recover' else run)(args.root.resolve(), args.design)
