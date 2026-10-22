#!/usr/bin/env python3
"""One fresh no-model initial flow per LC20 candidate, without repair or writeback.

Uses an existing hash-bound R4 candidate for qualification only. This is not a
release freeze, a paper repair episode, or authority to dispatch the growth curve.
The handoff and external baseline are read-only. No implicit retry/replacement.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import time

CPU_SLOTS = [list(range(53, 61)) + list(range(149, 157)),
             list(range(61, 69)) + list(range(157, 165))]
CONFIG_KEYS = {'DESIGN_NAME', 'PLATFORM', 'VERILOG_FILES', 'SDC_FILE',
               'CORE_UTILIZATION', 'ABC_AREA', 'SYNTH_MEMORY_MAX_BITS',
               'VERILOG_INCLUDE_DIRS'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')
    tmp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def verify_hashes(hashes):
    for name, expected in hashes.items():
        if not Path(name).is_file() or digest(name) != expected:
            raise ValueError('Registered input changed: ' + name)


def literal_config(text):
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'export\s+(\w+)\s*=\s*(.*?)\s*', line)
        if not match or match[1] not in CONFIG_KEYS or match[1] in out:
            raise ValueError('Not an allowed unique literal assignment: ' + line)
        if not re.fullmatch(r'[A-Za-z0-9_./+@:=, -]+', match[2]):
            raise ValueError('Executable/nonliteral config rejected: ' + match[1])
        out[match[1]] = match[2]
    return out


def inside(root, path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError('Source path escapes registered repository: ' + str(path))
    return resolved


def old_source(value, sources, repo):
    marker = '/sources/'
    if marker not in value:
        raise ValueError('Unregistered old source path: ' + value)
    rel = value.split(marker, 1)[1]
    return inside(repo, Path(sources) / rel)


def resolved_sdc(top, clock, period):
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', v or '') for v in (top, clock)):
        raise ValueError('Unresolved literal top/clock')
    if period != 5.0:
        raise ValueError('The supplied initial task clock must remain 5 ns')
    return ('# Resolve the supplied baseline real-clock choice; do not add IO delays.\n'
            '# Scope: internal sequential timing, not a complete board IO contract.\n'
            f'current_design {top}\nset clk_period {period:g}\n'
            f'set clk_port_name {clock}\n'
            'set clk_port [get_ports -quiet $clk_port_name]\n'
            'if {[llength $clk_port] != 1} {error "Registered real clock missing or ambiguous"}\n'
            'create_clock -name core_clk -period $clk_period $clk_port\n'
            'set_clock_uncertainty 0.0 [get_clocks *]\n'
            'puts "R2G_CLOCK real [get_name $clk_port]"\n')


def selected_rows(roster):
    selected = roster.get('selected', {})
    if len(selected.get('LEARN', [])) != 20 or len(selected.get('PROBE', [])) != 8:
        raise ValueError('Scope must remain exactly 20 LEARN plus 8 PROBE')
    rows = selected['LEARN'] + selected['PROBE']
    if len({r['design_id'] for r in rows}) != 28 or len({r['design'] for r in rows}) != 28:
        raise ValueError('Repeated design in fixed roster')
    if len({r['group_id'] for r in rows}) != 28:
        raise ValueError('Repeated lineage in fixed roster')
    if any(r.get('frontend') != 'verilog-native' or not r.get('baseline_clock_port') for r in rows):
        raise ValueError('This qualification supports registered native real-clock tasks only')
    return rows


def qualification_budget():
    return {'budget_id': 'learning-pilot20-no-model-qualification',
            'caps': {'flows': 1, 'llm_requests': 0, 'tokens_in': 0, 'tokens_out': 0,
                     'repair_attempts': 0, 'wall_s': 28800}, 'reserve': {}}


def clean_environment(raw):
    return {k: v for k, v in raw.items()
            if not k.startswith(('R2G_', 'TEHM_')) and k not in ('FROM_STAGE', 'ORFS_CPU_SET')}


def check_slots(slots, available):
    occupied = set(range(5, 13)) | set(range(27, 35))  # R4 physical cores; host has SMT offset 96
    for slot in slots:
        physical = {n % 96 for n in slot}
        if not slot or not set(slot) <= set(available) or physical & occupied:
            raise ValueError('CPU slot overlaps R4, another slot, or unavailable CPUs')
        occupied.update(physical)


def claim_attempt(path):
    try:
        with Path(path).open('x') as stream:
            json.dump({'status': 'CLAIMED', 'time': time.time()}, stream)
    except FileExistsError as exc:
        raise RuntimeError('Existing attempt; no overwrite or implicit retry') from exc


def qualification_rows(roster, rows, only_designs=None):
    if only_designs is None:
        return rows
    replacement = roster.get('qualification_amendment', {}).get('replacement_design')
    if not replacement or only_designs != [replacement]:
        raise ValueError('Subset must be exactly the registered new replacement; no old retry')
    selected = [row for row in rows if row['design'] == replacement]
    if len(selected) != 1:
        raise ValueError('Replacement missing from full registered roster')
    return selected


def prepare(root, roster_path, runtime, only_designs=None):
    if root.exists():
        raise RuntimeError('Existing qualification root; never overwrite or retry')
    began, cpu = time.time(), time.process_time()
    roster = load(roster_path)
    full_rows = selected_rows(roster)
    rows = qualification_rows(roster, full_rows, only_designs)
    verify_hashes(roster['input_hashes'])
    runtime_reg = load(runtime / 'registration.json')
    verify_hashes(runtime_reg['files'])
    if runtime_reg.get('platform') not in (None, 'sky130hd'):
        raise ValueError('Unexpected qualification runtime platform')
    check_slots(CPU_SLOTS, os.sched_getaffinity(0))
    corpus = Path(roster['source_corpus'])
    root.mkdir(parents=True)
    (root / 'operator').mkdir()
    shutil.copy2(__file__, root / 'operator' / Path(__file__).name)
    shutil.copy2(roster_path, root / 'roster.json')
    save(root / 'budget.json', qualification_budget())
    tasks, copied_hashes = [], {}
    for row in rows:
        manifest_path = Path(row['design_manifest'])
        manifest = load(manifest_path)
        repo = inside(corpus / 'sources', manifest_path.parent / manifest['source_dir'])
        original = literal_config(Path(row['initial_config']).read_text())
        if set(original) != CONFIG_KEYS or original['PLATFORM'] != 'sky130hd':
            raise ValueError('Unexpected initial settings: ' + row['design'])
        if original['DESIGN_NAME'] != manifest['top_module']:
            raise ValueError('Top differs from certified manifest')
        inputs = [old_source(v, corpus / 'sources', repo) for v in original['VERILOG_FILES'].split()]
        expected = {inside(repo, manifest_path.parent / f['file']): f['sha256']
                    for f in manifest['source_files']}
        if set(inputs) != set(expected):
            raise ValueError('Config RTL differs from certified source closure')
        verify_hashes({str(p): h for p, h in expected.items()})
        includes = [old_source(v, corpus / 'sources', repo)
                    for v in original['VERILOG_INCLUDE_DIRS'].split()]
        project = root / 'projects' / ('lc20q__' + row['design'].split('__')[0])
        for name in ('constraints', 'backend', 'reports', 'input', 'drc', 'lvs', 'rcx', 'lint', 'synth', 'sim'):
            (project / name).mkdir(parents=True)
        # Includes can use parents/relative sibling headers. Preserve repository
        # layout and legal documents, and reject any link escaping its root.
        for p in repo.rglob('*'):
            inside(repo, p)
        shutil.copytree(repo, project / 'rtl')
        cfg = dict(original)
        cfg['VERILOG_FILES'] = ' '.join(str(project / 'rtl' / p.relative_to(repo)) for p in inputs)
        cfg['VERILOG_INCLUDE_DIRS'] = ' '.join(str(project / 'rtl' / p.relative_to(repo)) for p in includes)
        cfg['SDC_FILE'] = str(project / 'constraints/constraint.sdc')
        config = project / 'constraints/config.mk'
        config.write_text(''.join('export ' + k + ' = ' + v + '\n' for k, v in cfg.items()))
        sdc = project / 'constraints/constraint.sdc'
        sdc.write_text(resolved_sdc(manifest['top_module'], row['baseline_clock_port'], 5.0))
        shutil.copy2(row['initial_constraints'], project / 'constraints/external-baseline.sdc.reference')
        shutil.copy2(manifest_path, project / 'constraints/source-DESIGN.json')
        for p in (project / 'rtl').rglob('*'):
            if p.is_file():
                copied_hashes[str(p)] = digest(p)
        for p in (project / 'constraints').iterdir():
            copied_hashes[str(p)] = digest(p)
        tasks.append({**row, 'project': str(project), 'variant': 'initial',
                      'expected_clock_period_ns': 5.0, 'config_sha256': digest(config),
                      'sdc_sha256': digest(sdc), 'clock_selection': 'literal CSV real clock',
                      'io_timing_scope': 'internal sequential timing; no IO delays in supplied baseline',
                      'paper_episode': False, 'learning_writeback_allowed': False})
    frozen = {str(root / 'roster.json'): digest(root / 'roster.json'),
              str(root / 'budget.json'): digest(root / 'budget.json'),
              str(root / 'operator' / Path(__file__).name): digest(__file__),
              str(runtime / 'registration.json'): digest(runtime / 'registration.json'), **copied_hashes}
    save(root / 'registration.json', {'schema': 'learning-pilot20-qualification-v1',
         'registered_at': time.time(), 'runtime_root': str(runtime),
         'runtime_status': 'R4_CANDIDATE_FOR_QUALIFICATION_ONLY_NOT_FINAL_FREEZE',
         'files': frozen, 'runtime_files': runtime_reg['files'], 'original_inputs': roster['input_hashes'],
         'tasks': tasks, 'cpu_slots': CPU_SLOTS, 'threads_per_worker': 8,
         'planned_flows': len(tasks), 'full_roster_size': len(full_rows),
         'qualification_subset': only_designs, 'llm_requests_authorized': 0, 'no_repair': True,
         'no_memory_write': True, 'no_implicit_retry': True, 'no_automatic_replacement': True,
         'no_formal_or_learning_dispatch': True, 'preparation_wall_s': time.time() - began,
         'preparation_cpu_s': time.process_time() - cpu,
         'safety_watchdogs_s': {'orfs_stage': 5400, 'initial_flow': 28800, 'signoff': 9000},
         'constraint_resolution': '5 ns real clock fixed from supplied CSV; original SDC retained; no added IO delays',
         'strict_acceptance': 'A1-A11; qualification status is not automatic repair-task admission'})
    save(root / 'progress.json', {'stage': 'PREPARED_NOT_STARTED', 'tasks': len(tasks), 'completed': 0,
                                  'model_requests': 0, 'learning_episodes_started': 0})
    print(json.dumps({'root': str(root), 'status': 'PREPARED', 'tasks': len(tasks),
                      'registration_sha256': digest(root / 'registration.json')}), flush=True)


def preflight(root):
    reg = load(root / 'registration.json')
    if reg.get('schema') != 'learning-pilot20-qualification-v1' or not reg.get('no_repair'):
        raise ValueError('Not a no-repair qualification registration')
    for key in ('files', 'runtime_files', 'original_inputs'):
        verify_hashes(reg[key])
    return reg


def worker(root, design):
    reg = preflight(root)
    row, = [r for r in reg['tasks'] if r['design'] == design]
    project = Path(row['project'])
    result_path = root / (design + '.json')
    claim_attempt(result_path)
    code = Path(reg['runtime_root']) / 'code'
    sys.path[:0] = [str(code / 'memory/scripts'), str(code / 'memory')]
    import r2g_memory_eval as H
    import t2a_runtime as T
    import t2a_task_manifests as TM
    H.SKILL = code / 'r2g-skills/signoff-loop'
    H.FLOW = H.SKILL / 'scripts/flow'
    H.EVAL = root
    H.CFG = {'platform': 'sky130hd'}
    H._EPISODE['ledger'] = project
    cleaned = clean_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(cleaned, R2G_BUDGET_FILE=str(root / 'budget.json'), R2G_REQUIRE_LLM_BUDGET='1')
    env = H.base_env('none')
    env.update(R2G_ENV_FILE=str(H.SKILL / 'references/env.local.sh'),
               R2G_JOURNAL_DB=str(project / 'qualification-journal.sqlite'),
               R2G_TEHM_ROOT=str(code / 'memory'), PYTHONPATH=str(code / 'memory') + ':' + str(code / 'memory/scripts'),
               R2G_BUDGET_COMPONENT='qualification', R2G_VERIFICATION_PURPOSE='final_acceptance',
               CUDA_VISIBLE_DEVICES='', PYTHONDONTWRITEBYTECODE='1', ORFS_TIMEOUT='5400')
    start, child0 = time.time(), resource.getrusage(resource.RUSAGE_CHILDREN)
    result = {**row, 'status': 'RUNNING', 'start': start,
              'cpu_affinity': sorted(os.sched_getaffinity(0)), 'registration_sha256': digest(root / 'registration.json')}
    save(result_path, result)
    log = project / 'qualification.log'
    try:
        rc, _ = H._ledger(project, 'event', '--type', 'attempt_start', '--json', json.dumps(
            {'phase': 'learning_qualification', 'task': design, 'repair_allowed': False,
             'registration_sha256': digest(root / 'registration.json')}))
        if rc:
            raise RuntimeError('Cannot start qualification ledger')
        result['orfs_rc'] = H.run(['bash', H.FLOW / 'run_orfs.sh', project, 'sky130hd'], env, log, 28800)
        H.run(['python3', H.SKILL / 'scripts/extract/extract_ppa.py', project, project / 'reports/ppa.json'], env, log, 600)
        runs = sorted((project / 'backend').glob('RUN_*'))
        newest = runs[-1] if runs else None
        final = bool(newest and ((newest / 'final/6_final.odb').is_file() or (newest / 'final/6_final.gds').is_file()))
        result['final_layout'] = final
        result['judged_run'] = newest.name if newest else None
        if final:
            H.run(['python3', H.SKILL / 'scripts/reports/check_timing.py', project], env, log, 600)
            result['signoff_rc'] = H.final_signoff(project, env, log)
            H.run(['python3', H.SKILL / 'scripts/reports/acceptance_check.py', project,
                   '--expected-clock-period', '5'], env, log, 300)
            result['acceptance'] = H.jload(project / 'reports/acceptance.json')
        else:
            result['acceptance'] = {'accepted': False, 'unknown': ['NO_FINAL_LAYOUT']}
        ppa = H.jload(project / 'reports/ppa.json')
        result['fail_stage'] = ppa.get('orfs_fail_stage') or (ppa.get('summary') or {}).get('orfs_fail_stage')
        try:
            manifest = TM.task_manifest(root, row)
            manifest.update(split=row['role'], paper_data=False, formal_launch_allowed=False,
                            qualification_status='REVIEW_REQUIRED', io_timing_scope=row['io_timing_scope'])
            save(project / 'task-envelope.json', manifest)
            result['area_reference_status'] = 'INITIAL_FLOORPLAN_REFERENCES_RECORDED'
            result['task_envelope_sha256'] = digest(project / 'task-envelope.json')
        except (OSError, ValueError, IndexError) as exc:
            result['area_reference_status'] = 'NOT_AVAILABLE'
            result['area_reference_error'] = str(exc)
        result['status'] = ('INITIAL_ACCEPTED' if result['acceptance'].get('accepted') and result.get('signoff_rc') == 0
                            else 'INITIAL_FAILURE_CANDIDATE' if final
                            else 'FRONTEND_OR_INFRA_REVIEW' if result['fail_stage'] in (None, 'synth')
                            else 'INITIAL_FAILURE_CANDIDATE')
        if result['orfs_rc'] in (75, 76, -9, 124, 137):
            result['status'] = 'SAFETY_STOP'
    except Exception as exc:
        result.update(status='INFRASTRUCTURE_ERROR', error=str(exc))
    finally:
        child1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(end=time.time(), wall_s=time.time() - start,
                      children_cpu_s=(child1.ru_utime + child1.ru_stime - child0.ru_utime - child0.ru_stime))
        ledger = project / 'reports/resource_ledger.jsonl'
        events = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []
        result['accounting'] = T.accounting_check(events, list((project / 'backend').glob('RUN_*')))
        result['token_usage'] = T.request_token_totals(events)
        result['tool_calls'] = sum(e.get('type') == 'tool_call' for e in events)
        result['checker_runs'] = sum(e.get('type') == 'checker_run' for e in events)
        rc, raw = H._ledger(project, 'summary', '--json')
        result['resources'] = json.loads(raw) if rc == 0 else 'UNKNOWN'
        if not result['accounting']['passed'] or result['accounting']['llm_requests'] != 0:
            result['status'] = 'ACCOUNTING_ERROR'
        if ledger.exists():
            result['ledger_sha256'] = digest(ledger)
        save(result_path, result)
        H._EPISODE['ledger'] = None
    print(json.dumps({'design': design, 'status': result['status'], 'wall_s': result['wall_s']}), flush=True)
    return 1 if result['status'] in ('INFRASTRUCTURE_ERROR', 'ACCOUNTING_ERROR') else 0


def batch(root):
    lock = (root / 'batch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    reg = preflight(root)
    check_slots(reg['cpu_slots'], os.sched_getaffinity(0))
    claim_attempt(root / 'batch-start.json')
    save(root / 'batch-start.json', {'start': time.time(), 'registration_sha256': digest(root / 'registration.json'),
                                   'model_requests_authorized': 0, 'automatic_repair_dispatch': False})
    pending, live, ended = list(reg['tasks']), {}, []
    script = root / 'operator' / Path(__file__).name
    last_load = 0
    while pending or live:
        for slot_id, slot in enumerate(reg['cpu_slots']):
            if slot_id in live:
                continue
            if not pending:
                break
            row = pending.pop(0)
            log = (root / (row['design'] + '.worker.log')).open('x')
            proc = subprocess.Popen(['taskset', '-c', ','.join(map(str, slot)), sys.executable,
                                     '-u', str(script), 'worker', '--root', str(root), '--design', row['design']],
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            live[slot_id] = (proc, row, log)
        for slot_id, (proc, row, log) in list(live.items()):
            code = proc.poll()
            if code is not None:
                log.close()
                ended.append({'design': row['design'], 'exit_status': code})
                del live[slot_id]
        save(root / 'progress.json', {'stage': 'QUALIFICATION_RUNNING' if live or pending else 'QUALIFICATION_COMPLETE_REVIEW_REQUIRED',
             'time': time.time(), 'completed': len(ended), 'total': len(reg['tasks']),
             'running': [row['design'] for proc, row, log in live.values()], 'remaining': len(pending),
             'model_requests': 0, 'learning_episodes_started': 0, 'no_automatic_learning_dispatch': True})
        if time.time() - last_load >= 30:
            with (root / 'machine-load.jsonl').open('a') as stream:
                stream.write(json.dumps({'time': time.time(), 'load': list(os.getloadavg()),
                                         'running': [row['design'] for proc, row, log in live.values()]}) + '\n')
            last_load = time.time()
        if pending or live:
            time.sleep(2)
    save(root / 'batch-result.json', {'status': 'COMPLETE_REVIEW_REQUIRED', 'ended': time.time(),
         'tasks': ended, 'planned_flows': len(reg['tasks']), 'llm_requests': 0, 'paper_data': False,
         'repair_admission': 'Native failures, unknown acceptance, area and IO scope need explicit review.',
         'learning_dispatch_allowed': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--roster', type=Path, required=True)
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--only-design', action='append')
    for command in ('batch', 'worker'):
        p = sub.add_parser(command)
        p.add_argument('--root', type=Path, required=True)
        if command == 'worker':
            p.add_argument('--design', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.root.resolve(), args.roster.resolve(), args.runtime.resolve(), args.only_design)
    elif args.command == 'batch':
        batch(args.root.resolve())
    else:
        return worker(args.root.resolve(), args.design)
    return 0


if __name__ == '__main__':
    sys.exit(main())
