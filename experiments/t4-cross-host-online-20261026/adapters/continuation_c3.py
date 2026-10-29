"""C3: only the final never-started RTLScout TEHM task, no retries or reruns."""
import json
import time

from bridge import ROOT
from continuation_c2 import sha, verify_files

PATH = ROOT / 'inputs/continuation-runtime-c3.json'
ROSTER = [('rtlscout', 'tehm', 'fifo_sync4')]


def verify_base_delta(base, current):
    allowed = {'adapters/run_scout.py', 'adapters/phase_gate.py',
               'adapters/summarize_integration.py'}
    changed = [name for name, value in base.items()
               if name.endswith('.py') and current.get(name) != value]
    if set(changed) - allowed:
        raise ValueError('unregistered change from C2 runtime: ' + str(changed))
    return sorted(changed)


def unstarted(host, arm, task, root=ROOT):
    if (host, arm, task) not in ROSTER:
        raise ValueError('outside the one-episode C3 continuation')
    directory = root / 'hosts' / host / arm / 'episodes'
    if any(p.is_dir() and p.name.startswith(task + '__') for p in directory.glob('*')):
        raise ValueError('episode already allocated; never rerun')


def validate():
    data = json.loads(PATH.read_text())
    if data['roster'] != [dict(zip(('host', 'arm', 'task'), item)) for item in ROSTER]:
        raise ValueError('C3 roster changed')
    verify_files(ROOT, data['files'])
    return data


def require_host(host):
    if host != 'rtlscout':
        raise ValueError('C3 is only qualified for RTLScout')
    return validate()


def prepare():
    from phase_gate import require_clear
    from qualify_continuation import read_suite
    if PATH.exists():
        raise ValueError('never overwrite a prepared or executed C3 freeze')
    require_clear()
    for row in ROSTER:
        unstarted(*row)
    tests = read_suite('modern-c3-tests.xml', [
        'test_c3_roster_is_only_the_never_started_scout_tehm',
        'test_c3_exact_unknown_grant_keeps_usage_unknown_and_blocks_future_loss',
        'test_c3_runtime_worker_exposes_the_registered_flag_without_requests',
        'test_native_local_execution_and_verified_rollback_without_llm[False]'])
    files = {p for base in [ROOT/'adapters', ROOT/'code/memory', ROOT/'sources/rtlscout',
                            ROOT/'sources/rtlfixer/src', ROOT/'sources/verilog-eval/verilog_eval']
             for p in base.rglob('*.py') if '__pycache__' not in p.parts}
    files.update(p for p in (ROOT/'sources/rtlscout/benchmarks/fifo_sync4').rglob('*') if p.is_file())
    files.update(ROOT/name for name in (
        'inputs/first-batch.json', 'inputs/empty.env', 'inputs/continuation-authorization-c3.json',
        'inputs/continuation-runtime-c2.json', 'reports/modern-c3-tests.xml'))
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}
    base = json.loads((ROOT/'inputs/continuation-runtime-c2.json').read_text())
    delta = verify_base_delta(base['files'], hashes)
    data = {'schema': 'table4-continuation-c3', 'time': time.time(), 'paper_data': False,
            'roster': [dict(zip(('host', 'arm', 'task'), row)) for row in ROSTER],
            'tests': tests, 'software_withheld': 4, 'no_episode_reruns': True,
            'new_unknown_requires_decision': True,
            'delta_from_c2': delta, 'files': hashes}
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
    return data


if __name__ == '__main__':
    print(json.dumps({'files': len(prepare()['files'])}))
