#!/usr/bin/env python3
"""Read-only native legalization replay of the two observed DEV states.

This is not a repair episode, full ORFS flow or paper result. Source ODBs and
all original episode records remain untouched; no model is called.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

SOURCE = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2a-real-seven-arm-dev-20261012-b/episodes/riscv_core__u19__TM0__r1')
OPENROAD = Path('/data1/zhangdy/Tools/tehm-toolchain/openroad-matched/bin/openroad')
LIB = Path('/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow/platforms/sky130hs/lib/sky130_fd_sc_hs__tt_025C_1v80.lib')
LEDGER = Path('/data1/zhangdy/tehm-integration/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py')
CASES = (('failed_catalogue', 'RUN_2026-10-12_03-57-28_1349162_123b', 4, False),
         ('pre_catalogue', 'RUN_2026-10-12_03-27-23_1202470_2064', 2, True))


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, data):
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    (root/'reports').mkdir()
    start, cpu = time.time(), time.process_time()
    inputs = {str(p):digest(p) for p in (SOURCE/'episode.json', OPENROAD, LIB, LEDGER, Path(__file__))}
    commands = []
    for label, tag, padding, expect_pass in CASES:
        run_dir = SOURCE/'backend'/tag
        odb = run_dir/'results/3_4_place_resized.odb'
        receipt = run_dir/'effective-config.json'
        cfg = json.loads(receipt.read_text())['effective']
        assert cfg['CORE_UTILIZATION']['value']=='27'
        assert cfg['CELL_PAD_IN_SITES_DETAIL_PLACEMENT']['value']==str(padding)
        assert cfg['CELL_PAD_IN_SITES_GLOBAL_PLACEMENT']['value']==('4' if padding==4 else '0')
        inputs.update({str(odb):digest(odb), str(receipt):digest(receipt)})
        script = root/(label+'.tcl')
        script.write_text('set_thread_count 8\nread_liberty {'+str(LIB)+'}\nread_db {'+str(odb)+'}\n'
            'set_placement_padding -global -left '+str(padding)+' -right '+str(padding)+'\n'
            'if {[catch {detailed_placement; check_placement -verbose} reason]} {\n'
            '  puts stderr $reason\n  exit 2\n}\nputs {NATIVE_LEGALIZATION_OK}\nexit 0\n')
        inputs[str(script)] = digest(script)
        commands.append({'label':label, 'run_tag':tag, 'padding':padding, 'expect_pass':expect_pass,
                         'command':[str(OPENROAD), '-exit', str(script)]})
    save(root/'registration.json', {'schema':'t2a-restoration-native-placement-probe-v1',
        'registered_at':time.time(), 'paper_data':False, 'split':'DEV_DIAGNOSTIC',
        'new_llm_requests':0, 'full_flows':0, 'native_tool_runs':2,
        'hypothesis':'Failed catalogue util27/global4/detail4 is infeasible; pre-step util27/global0/detail2 legalizes',
        'not_a_full_signoff_or_repair_result':True,
        'commands':commands, 'input_hashes':inputs, 'cpu_affinity':sorted(os.sched_getaffinity(0))})
    env = {k:v for k,v in os.environ.items() if not k.startswith(('R2G_BUDGET', 'R2G_LEDGER', 'R2G_PHASE'))}
    results = []
    for row in commands:
        t0 = time.time(); before = resource.getrusage(resource.RUSAGE_CHILDREN)
        with (root/(row['label']+'.log')).open('w') as log:
            process = subprocess.Popen(row['command'], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try: rc = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9); process.wait(); rc = 124
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        text = (root/(row['label']+'.log')).read_text()
        passed = rc==0 and 'NATIVE_LEGALIZATION_OK' in text
        expected = passed if row['expect_pass'] else rc==2 and 'DPL-0036' in text
        result = {**row, 'rc':rc, 'wall_s':time.time()-t0,
            'cpu_s':(after.ru_utime+after.ru_stime)-(before.ru_utime+before.ru_stime),
            'peak_rss_kib':after.ru_maxrss, 'rss_scope':'cumulative native children maximum, not additive',
            'legalization_passed':passed, 'expected_outcome_observed':bool(expected),
            'log_sha256':digest(root/(row['label']+'.log')), 'type':'tool_call',
            'component':'DEV_restoration_diagnostic', 'tool':'openroad', 'timeout':rc==124}
        subprocess.run([sys.executable, str(LEDGER), 'event', str(root), '--type', 'tool_call',
                        '--json', json.dumps(result)], env=env, check=True, stdout=subprocess.DEVNULL)
        results.append(result)
        save(root/'progress.json', {'results':results})
    unchanged = all(digest(Path(p))==h for p,h in inputs.items())
    save(root/'result.json', {'paper_data':False, 'split':'DEV_DIAGNOSTIC',
        'native_legalization_hypothesis_confirmed':unchanged and all(r['expected_outcome_observed'] for r in results),
        'full_episode_validated':False, 'original_inputs_unchanged':unchanged, 'results':results,
        'wall_s':time.time()-start, 'controller_cpu_s':time.process_time()-cpu,
        'llm_requests':0, 'llm_tokens':0, 'full_flows':0})
    return 0 if unchanged and all(r['expected_outcome_observed'] for r in results) else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args=parser.parse_args()
    sys.exit(run(args.root.resolve()))
