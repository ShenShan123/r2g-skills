#!/usr/bin/env python3
"""Isolated, instrumented full-deck profiling; never changes campaign results."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import xml.etree.ElementTree as ET

from probe_nangate45_drc_mode import compare, invoke, now, save, sha


SMALL = ['exp1_bb6ca826aab0_control_top', 'exp1_ea25af7ff4a7_serv_rf_top',
         'exp1_da166bbae20c_xtea']
LARGE = 'exp1_e89732799486_qmap_row1024_axi_smoke_bd'


def instrument(data, threads):
    if threads not in (1, 4):
        raise ValueError('Only one/four-thread comparison supported')
    root = ET.fromstring(data)
    node = root.find('text')
    if node is None or not node.text:
        raise ValueError('Missing DRC script')
    code = node.text
    if code.count('threads(4)') != 1 or '\ndeep\n' not in code:
        raise ValueError('Unexpected execution configuration')
    if 'cont.not(active).not(poly).not(metal1)' not in code:
        raise ValueError('Expected validated contact-difference deck')
    calls = [line for line in code.splitlines() if line.startswith('antenna_check(')]
    if len(calls) != 10:
        raise ValueError('Expected ten antenna checks')
    for i, line in enumerate(calls, 1):
        if not line.startswith(f'antenna_check(gate, metal{i}, 300.0, diode).output('):
            raise ValueError('Unexpected antenna rule')
    code = code.replace('threads(4)', f'threads({threads})', 1)
    helper = '''
$stdout.sync = true
def r2g_profile_phase(label)
  started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  cpu = Process.times
  puts "R2G_PROFILE_BEGIN #{label}"
  yield
  ended = Process.times
  wall = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started
  used = ended.utime + ended.stime - cpu.utime - cpu.stime
  puts "R2G_PROFILE_END #{label} #{wall} #{used}"
end
'''
    # Explicit extraction moves deferred setup out of the first antenna timing.
    code = helper + code
    code = code.replace(calls[0], 'r2g_profile_phase("netlist_extract") { netlist }\n'+calls[0], 1)
    for i, line in enumerate(calls, 1):
        code = code.replace(line, f'r2g_profile_phase("metal{i}") do\n{line}\nend', 1)
    node.text = code
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def timings(text):
    phases = {}
    active = None
    for line in text.splitlines():
        if line.startswith('R2G_PROFILE_BEGIN '):
            active = line.split()[1]
        elif line.startswith('R2G_PROFILE_END '):
            _, name, wall, cpu = line.split()
            phases[name] = dict(wall_seconds=float(wall), cpu_seconds=float(cpu))
            active = None
    return dict(phases=phases, unfinished_phase=active)


def log_operations(text):
    operations = []
    current = None
    for line in text.splitlines():
        match = re.match(r'^"([^"]+)" in: (.*):(\d+)$', line)
        if match:
            current = dict(operation=match[1], deck=match[2], line=int(match[3]))
        elapsed = re.search(r'Elapsed: ([0-9.]+)s', line)
        if elapsed and current:
            operations.append(dict(current, seconds=float(elapsed[1])))
            current = None
    total = re.search(r'Total elapsed: ([0-9.]+)s', text)
    return dict(total_seconds=float(total[1]) if total else None,
                operations=operations,
                largest=sorted(operations, key=lambda x: x['seconds'], reverse=True)[:10])


def prepare(args):
    root = args.root.resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError('Use a new analysis directory')
    previous = args.previous_probe
    evidence = json.loads((previous/'status.json').read_text())
    if not evidence['all_canaries_match']:
        raise ValueError('Contact optimization was not validated')
    source = previous/'FreePDK45.contact_difference.lydrc'
    cases = []
    for task in SMALL:
        result = next(r for r in evidence['results'] if r['task_id'] == task)
        if result['status'] != 'MATCH' or not result['reference_inputs_unchanged']:
            raise ValueError('Invalid canary evidence')
        command = result['command']
        gds = Path(next(x[len('in_gds='):] for x in command if x.startswith('in_gds=')))
        reference = previous/task/'contact_difference.lyrdb'
        if sha(reference) != result['trial_report_sha256']:
            raise ValueError('Reference report changed')
        old_plan = json.loads((previous/'plan.json').read_text())
        old_case = next(c for c in old_plan['cases'] if c['task_id'] == task)
        if sha(gds) != old_case['input_hashes'][str(gds)]:
            raise ValueError('GDS changed')
        cases.append(dict(task_id=task, gds=str(gds), reference_report=str(reference),
                          reference_wall_seconds=result['wall_seconds'], threads=[1, 4]))
    project = args.baseline/'jobs'/LARGE/'project'
    meta_path = project/'drc/drc_result.json'
    meta = json.loads(meta_path.read_text())
    if meta['exit_code'] != 0 or meta['status'] not in ('clean', 'violations') or meta['drc_mode'] != 'full':
        raise ValueError('Large reference incomplete')
    gds = Path(meta['gds_path'])
    if sha(gds) != meta['gds_sha256'] or sha(source) != meta['deck_sha256']:
        raise ValueError('Large reference differs from validated inputs')
    reference = project/'backend'/meta['run_tag']/'drc/6_drc.lyrdb'
    cases.append(dict(task_id=LARGE, gds=str(gds), reference_report=str(reference),
                      reference_wall_seconds=meta['wall_s'], threads=[4]))
    root.mkdir(parents=True, exist_ok=True)
    decks = {}
    for threads in (1, 4):
        path = root/f'profile_t{threads}.lydrc'
        path.write_bytes(instrument(source.read_bytes(), threads))
        decks[str(threads)] = str(path)
    for case in cases:
        case['input_hashes'] = {p: sha(p) for p in (case['gds'], case['reference_report'])}
    plan = dict(created_at=now(), cases=cases, decks=decks, source_deck=str(source),
                cpu_set=[180,181,182,183], klayout='/usr/bin/klayout', timeout_seconds=3600,
                version=subprocess.check_output(['/usr/bin/klayout','-v'], text=True).strip(),
                baseline_modified=False, auto_rollout=False,
                purpose='Separate deferred network extraction from antenna area checks; thread scaling',
                limits='One pass per thread setting; timings sensitive to caches and shared host load')
    if plan['version'] != meta['klayout_version']:
        raise ValueError('KLayout version changed')
    plan['implementation_hashes'] = {str(p):sha(p) for p in
        [Path(__file__), Path(__file__).with_name('probe_nangate45_drc_mode.py'), source,
         Path('/usr/bin/klayout'), *map(Path,decks.values())]}
    save(root/'plan.json', plan)
    historical = {}
    for task in [*SMALL, LARGE, 'exp1_6016dd9fd67c_ddr_sdram_ctrl',
                 'exp1_f882cf5b3bd4_async_fifo', 'exp1_053aca737220_te_array']:
        logs = sorted((args.baseline/'jobs'/task/'project/backend').glob('RUN_*/drc/6_drc.log'))
        if logs:
            historical[task] = dict(log=str(logs[-1]), **log_operations(logs[-1].read_text(errors='replace')))
    save(root/'historical_profile.json', dict(captured_at=now(), designs=historical))
    print(json.dumps(dict(status='prepared', runs=sum(len(c['threads']) for c in cases))), flush=True)


def run(args):
    root = args.root.resolve()
    plan = json.loads((root/'plan.json').read_text())
    with (root/'profile.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for p, expected in plan['implementation_hashes'].items():
            if sha(p) != expected:
                raise ValueError('Analysis code changed: '+p)
        results = []
        for case in plan['cases']:
            for threads in case['threads']:
                work = root/case['task_id']/f't{threads}'
                if (work/'result.json').exists():
                    results.append(json.loads((work/'result.json').read_text()))
                    continue
                if work.exists():
                    raise RuntimeError('Partial probe needs inspection: '+str(work))
                if not all(sha(p)==h for p,h in case['input_hashes'].items()):
                    raise ValueError('Reference changed')
                work.mkdir(parents=True)
                cpu_set=plan['cpu_set'][:threads]
                os.sched_setaffinity(0,set(cpu_set))
                save(root/'status.json',dict(status='running',active=case['task_id'],threads=threads,
                                            started_at=now(),results=results))
                output = work/'profile.lyrdb'
                command=[plan['klayout'],'-zz','-rd','in_gds='+case['gds'],
                         '-rd','report_file='+str(output),'-r',plan['decks'][str(threads)]]
                usage=resource.getrusage(resource.RUSAGE_CHILDREN)
                result=invoke(command,work/'profile.log',plan['timeout_seconds'])
                after=resource.getrusage(resource.RUSAGE_CHILDREN)
                result.update(task_id=case['task_id'],threads=threads,cpu_set=cpu_set,
                              completed_at=now(),command=command,
                              process_cpu_seconds=after.ru_utime+after.ru_stime-usage.ru_utime-usage.ru_stime,
                              **timings((work/'profile.log').read_text(errors='replace')))
                if result['returncode']==0 and output.exists():
                    result['comparison']=compare(case['reference_report'],output)
                    result['status']='MATCH' if result['comparison']['equivalent_on_this_case'] else 'MISMATCH'
                    if len(result['phases'])!=11 or result['unfinished_phase']:
                        result['status']='PROFILE_INCOMPLETE'
                    result['report_sha256']=sha(output)
                else:
                    result['status']='TIMEOUT' if result['timed_out'] else 'EXECUTION_FAILURE'
                result['reference_inputs_unchanged']=all(sha(p)==h for p,h in case['input_hashes'].items())
                save(work/'result.json',result)
                results.append(result)
                print(json.dumps(result),flush=True)
                if result['status']!='MATCH' or not result['reference_inputs_unchanged']:
                    save(root/'status.json',dict(status='stopped_for_review',results=results))
                    return
        save(root/'status.json',dict(status='complete',completed_at=now(),results=results,
                                    baseline_modified=False,auto_rollout=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--previous-probe',type=Path)
    args=parser.parse_args()
    if args.command=='prepare':
        if not args.baseline or not args.previous_probe:
            parser.error('prepare requires baseline and previous-probe')
        prepare(args)
    else:
        run(args)
