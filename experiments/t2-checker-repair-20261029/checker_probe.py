"""Isolated, hash-bound checker diagnosis. Never executes ORFS or edits old runs."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASE = HERE.parent/'t2-freepdk45-main50-adamem-20261022-r3'
TOOLS = HERE.parents[1]/'Tools/tehm-toolchain'
OLD = TOOLS/'klayout/bin/klayout.bin'
NEW = TOOLS/'klayout-0.30.12-isolated/klayout'
FLOW = HERE.parents[1]/'Tools/OpenROAD-flow-scripts-clean/flow'
CRASH = re.compile(r'Signal number|dbLayoutVsSchematicWriter|Internal error|compare raised exception:|report_lvs failed:', re.I)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def atomic_json(path, data):
    path = Path(path); tmp = path.with_name(path.name+'.tmp')
    with tmp.open('x') as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False); stream.write('\n')
    os.replace(tmp, path)


def new_directory(path): Path(path).mkdir(parents=True, exist_ok=False)


def check_hashes(pins):
    for name, digest in pins.items():
        if not Path(name).is_file() or sha(name) != digest: raise ValueError('Input drift: '+name)


def check_output(output, inputs):
    output = output.resolve()
    for source in inputs:
        source = source.resolve()
        if output == source or source.is_relative_to(output): raise ValueError('Output encloses input')
        for parent in source.parents:
            if parent.parent.name == 'episodes' and output.is_relative_to(parent):
                raise ValueError('Output is inside historical episode')


def drc_verdict(code, database, log):
    if code: return {'status': 'UNKNOWN', 'reason': 'checker_timeout' if code == 124 else 'checker_nonzero_exit'}
    if CRASH.search(log): return {'status': 'UNKNOWN', 'reason': 'checker_crash'}
    try: root = ET.parse(database).getroot()
    except (OSError, ET.ParseError): return {'status': 'UNKNOWN', 'reason': 'missing_or_incomplete_database'}
    if root.tag != 'report-database' or root.find('items') is None:
        return {'status': 'UNKNOWN', 'reason': 'invalid_database_schema'}
    count = len(root.findall('./items/item'))
    return {'status': 'clean' if count == 0 else 'fail', 'violations': count}


def lvs_verdict(code, database, log, native=None):
    if code: return {'status': 'UNKNOWN', 'reason': 'checker_timeout' if code == 124 else 'checker_nonzero_exit'}
    if CRASH.search(log): return {'status': 'UNKNOWN', 'reason': 'checker_crash'}
    if not database.is_file() or not native or native.get('circuit_pairs', 0) < 1:
        return {'status': 'UNKNOWN', 'reason': 'database_not_independently_readable'}
    good = 'CONGRATULATIONS! Netlists match.' in log
    bad = "ERROR : Netlists don't match" in log
    if good and not bad and native.get('status') == 'clean': return {'status': 'clean'}
    if bad and not good and native.get('status') == 'fail': return {'status': 'fail'}
    return {'status': 'UNKNOWN', 'reason': 'database_log_disagreement'}


def bound_run(command, cwd, log, seconds):
    """Own process group; reap descendants even if the wrapper exits first."""
    start = time.monotonic(); before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = None; timed_out = False
    try:
        with log.open('xb') as stream:
            process = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try: process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: pass
    finally:
        if process is not None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {'exit_code': 124 if timed_out else process.returncode, 'wall_s': time.monotonic()-start,
            'cpu_s': after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            'peak_rss_kb': after.ru_maxrss, 'process_group': process.pid, 'timed_out': timed_out}


def tool_pins(exe):
    files = {str(exe): sha(exe)}
    if exe == NEW:
        root = NEW.parent/'root/usr'
        for p in [root/'bin/klayout', *sorted((root/'lib/klayout').glob('*.so*'))]:
            if p.is_file(): files[str(p)] = sha(p)
    return files


def prepare():
    """Four representative layouts; both old/new tools only on cheap controls."""
    data = json.loads((BASE/'table2-results.json').read_text()); plans=[]
    choices=[('C02982', 'NB', 'drc', 'old', 180), ('C02982', 'NB', 'drc', 'new', 180),
             ('C02982', 'NB', 'lvs', 'old', 180), ('C02982', 'NB', 'lvs', 'new', 180),
             ('C00517', 'NB', 'drc', 'new', 1200),
             ('C02133', 'RECIPE', 'lvs', 'new', 1800),
             ('C00651', 'RECIPE', 'lvs', 'new', 1800)]
    for ident, arm, kind, version, seconds in choices:
        row = next(r for r in data['episodes'] if r['design']==ident and r['arm']==arm)
        project = Path(row['episode']).parent
        report = project/'reports'/f'{kind}.json'; recorded = json.loads(report.read_text())
        gds=Path(recorded['gds_path'])
        if not gds.is_file():
            run=Path(recorded['provenance']['run_dir'])
            candidates=[p for sub in ('results','final') if (p:=run/sub/'6_final.gds').is_file() and sha(p)==recorded['gds_sha256']]
            if not candidates: raise ValueError('Historical GDS missing or changed: '+str(project))
            gds=candidates[0]
        if sha(gds)!=recorded['gds_sha256']: raise ValueError('GDS binding mismatch')
        deck = FLOW/'platforms/nangate45'/kind/('FreePDK45.lydrc' if kind=='drc' else 'FreePDK45.lylvs')
        if sha(deck)!=recorded.get('deck_sha256' if kind=='drc' else 'rule_sha256'):
            raise ValueError('Deck differs from historical checker')
        pins={str(p):sha(p) for p in (report, Path(row['episode']), gds, deck, HERE/'checker_probe.py', HERE/'validate_lvsdb.rb')}
        inputs={'gds':str(gds), 'deck':str(deck)}
        if kind=='lvs':
            cdl=list((project/'.orfs-work/objects').glob('nangate45/*/*/6_final_concat.cdl'))
            if len(cdl)!=1: raise ValueError('No unique preserved concatenated CDL')
            inputs['cdl']=str(cdl[0]); pins[str(cdl[0])]=sha(cdl[0])
        exe = OLD if version=='old' else NEW; pins.update(tool_pins(exe))
        plans.append({'id':f'{ident}-{kind}-{version}', 'kind':kind, 'source_arm':arm,
            'tool':str(exe), 'version':subprocess.check_output([str(exe),'-v'],text=True).strip(),
            'inputs':inputs, 'pins':pins, 'timeout_s':seconds, 'historical_status':recorded.get('status'),
            'historical_run':recorded.get('run_tag'), 'cdl_historical_digest_available':False if kind=='lvs' else None})
    manifest={'paper_data':False, 'policy':'checker-only; unchanged complete decks; no ORFS; no model; no old record edits',
              'diagnostic_time_limits_not_formal_caps':True, 'jobs':plans, 'created':time.time()}
    path=HERE/'plan-v2.json'
    if path.exists(): raise FileExistsError(path)
    atomic_json(path,manifest)
    return manifest


def run(ident):
    manifest=json.loads((HERE/'plan-v2.json').read_text());job=next(j for j in manifest['jobs'] if j['id']==ident)
    out=HERE/'probes-v2'/ident
    check_output(out,[Path(p) for p in job['pins']]);check_hashes(job['pins']);new_directory(out)
    inputs={}
    for key, value in job['inputs'].items():
        p=out/('input'+Path(value).suffix if key=='gds' else 'reference.cdl' if key=='cdl' else Path(value).name)
        shutil.copyfile(value,p);inputs[key]=p
    input_pins={str(v):sha(v) for v in inputs.values()}
    database=out/('6_drc.lyrdb' if job['kind']=='drc' else '6_lvs.lvsdb')
    cmd=[job['tool'],'-zz','-rd','in_gds='+str(inputs['gds']),'-rd','report_file='+str(database)]
    if job['kind']=='lvs':cmd+=['-rd','cdl_file='+str(inputs['cdl']),'-rd','target_netlist='+str(out/'extracted.cir')]
    cmd+=['-r',str(inputs['deck'])]
    atomic_json(out/'running.json',{'pid':os.getpid(),'job':ident,'started':time.time(),'command':cmd})
    timing=bound_run(cmd,out,out/'checker.log',job['timeout_s']);log=(out/'checker.log').read_text(errors='replace')
    native=None; native_timing=None
    if job['kind']=='lvs' and timing['exit_code']==0 and database.is_file() and not CRASH.search(log):
        native_path=out/'native-database-check.json'
        native_timing=bound_run([job['tool'],'-zz','-rd','input='+str(database),'-rd','output='+str(native_path),
                               '-r',str(HERE/'validate_lvsdb.rb')],out,out/'native-database-check.log',120)
        if native_timing['exit_code']==0 and native_path.exists():native=json.loads(native_path.read_text())
    verdict = drc_verdict(timing['exit_code'], database, log) if job['kind']=='drc' else lvs_verdict(timing['exit_code'],database,log,native)
    drift=[]
    for pins in (job['pins'],input_pins):
        try:check_hashes(pins)
        except ValueError as exc:drift.append(str(exc))
    if drift:verdict={'status':'UNKNOWN','reason':'input_or_tool_drift','errors':drift}
    receipt={'paper_data':False,'job':job,'command':cmd,'timing':timing,'native_check':native,'native_check_timing':native_timing,
             'verdict':verdict,'inputs_unchanged':not drift,'ended':time.time(),
             'database_sha256':sha(database) if database.is_file() else None,
             'model_requests':0,'eda_flows':0,'changes_historical_verdict':False}
    atomic_json(out/'receipt.json',receipt);return receipt


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','run']);ap.add_argument('job',nargs='?')
    args=ap.parse_args()
    if args.action=='prepare': print(json.dumps({'prepared_jobs':len(prepare()['jobs'])}))
    else:
        if not args.job:ap.error('run requires job id')
        result=run(args.job);print(json.dumps({'job':args.job,'verdict':result['verdict'],'wall_s':result['timing']['wall_s']}))
