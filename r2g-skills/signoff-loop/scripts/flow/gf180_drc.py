#!/usr/bin/env python3
"""GF180 baseline DRC: assembled flat deck, official fallback on no verdict.

The baseline deliberately excludes density; record that scope on every result.
Never accept partial official report sets or a changed layout as clean.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

SWITCHES = ('metal_top=9K', 'mim_option=B', 'metal_level=5LM', 'feol=true',
            'beol=true', 'conn_drc=true', 'run_mode=flat', 'offgrid=false', 'thr=4')
NOTE = 'GF180 baseline scope: FEOL/BEOL/connectivity/antenna; excludes metal density. Fast deck offgrid=false; official fallback uses its default offgrid checks.'
_child = None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stop_child():
    if _child is not None:
        try: os.killpg(_child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        _child.wait()


def cancelled(signum, _frame):
    stop_child()
    raise SystemExit(128 + signum)


def run(cmd, cwd, log, timeout, env=None):
    global _child
    with log.open('w') as out:
        _child = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return _child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_child()
            return 124
        finally:
            stop_child()
            _child = None


def reviewed_rules(rule_dir):
    manifest = json.loads((Path(__file__).parent/'platforms/gf180/drc_tables.json').read_text())
    tables = [rule_dir/name for name in manifest['tables']]
    if not any(p.name == 'antenna.drc' for p in tables):
        raise ValueError('GF180 antenna table missing')
    paths = [rule_dir/'main.drc', *tables, rule_dir/'tail.drc']
    raw = b''.join(p.read_bytes() for p in paths)
    if raw.count(b'Executing rule') < 600:
        raise ValueError('Incomplete GF180 rule set')
    if hashlib.sha256(raw).hexdigest() != manifest['baseline_deck_sha256'] or sha(rule_dir/'layers_def.drc') != manifest['baseline_layers_sha256']:
        raise ValueError('GF180 rule deck differs from the reviewed baseline')
    return raw, {p.name: sha(p) for p in [*paths, rule_dir/'layers_def.drc']}


def assemble(rule_dir, destination):
    raw, sources = reviewed_rules(rule_dir)
    destination.mkdir()
    (destination/'main.drc').write_bytes(raw)
    shutil.copy2(rule_dir/'layers_def.drc', destination/'layers_def.drc')
    return sources


def report_items(path):
    tree = ET.parse(path)
    if tree.getroot().tag != 'report-database' or tree.getroot().find('items') is None:
        raise ValueError('Incomplete KLayout report')
    return len(tree.findall('.//items/item'))


def merge_official(rule_dir, out, gds_stem, destination):
    tables = {p.stem for p in rule_dir.glob('*.drc')
              if not any(n in p.name for n in ('antenna', 'density', 'main', 'layers_def', 'tail'))}
    tables.add('antenna')
    expected = {out/(gds_stem+'_'+t+'.lyrdb') for t in tables}
    actual = set(out.glob('*.lyrdb'))
    if actual != expected:
        raise ValueError('Official GF180 report set incomplete or unexpected')
    trees = [ET.parse(p) for p in sorted(expected)]
    for p in expected: report_items(p)
    merged = copy.deepcopy(trees[0].getroot())
    for part in ('categories', 'cells', 'items'):
        node = merged.find(part)
        if node is None: raise ValueError('Missing report section: '+part)
        for tree in trees[1:]:
            section = tree.getroot().find(part)
            if section is None: raise ValueError('Missing report section: '+part)
            for child in section: node.append(copy.deepcopy(child))
    ET.ElementTree(merged).write(destination, encoding='utf-8', xml_declaration=True)
    return len(expected)


def main():
    p = argparse.ArgumentParser()
    for n in ('project', 'gds', 'pdk', 'klayout', 'top'): p.add_argument('--'+n, required=True)
    p.add_argument('--timeout', type=int, default=21600)
    a = p.parse_args();project=Path(a.project);gds=Path(a.gds)
    out=project/'drc';out.mkdir(exist_ok=True)
    # Separate attempts preserve logs, decks and incomplete report sets.
    attempt=out/('gf180_'+str(time.time_ns()));attempt.mkdir()
    canonical=out/'6_drc.lyrdb'
    for n in ('6_drc.lyrdb', '6_drc_count.rpt', 'drc_result.json'):
        (out/n).unlink(missing_ok=True)
    digest=sha(gds);start=time.time();rule_dir=Path(a.pdk)/'gf180mcuC/libs.tech/klayout/drc/rule_decks'
    record=dict(status='failed', checker='gf180_assembled_deck', drc_mode='full',
                note=NOTE, gds_path=str(gds), gds_sha256=digest,
                timeout_s=a.timeout, started_at=time.strftime('%FT%T%z'), attempt=str(attempt))
    try:
        sources=assemble(rule_dir, attempt/'deck');deck=attempt/'deck/main.drc'
        record.update(deck_path=str(deck),deck_sha256=sha(deck),rule_sources_sha256=sources,
                      switches=list(SWITCHES),klayout_version=subprocess.check_output(
                          [a.klayout,'-b','-v'],text=True,timeout=20).strip())
        report=attempt/'fast.lyrdb'
        cmd=[a.klayout,'-b','-r',str(deck)]
        for sw in (f'input={gds}',f'topcell={a.top}',f'report={report}',*SWITCHES):cmd+=['-rd',sw]
        rc=run(cmd,deck.parent,attempt/'fast.log',a.timeout);record['fast_exit_code']=rc
        try:
            if rc: raise ValueError('Fast DRC has no successful verdict')
            count=report_items(report)
        except (ValueError, OSError, ET.ParseError):
            # The PDK runner drives its own per-table processes. Validate EVERY
            # expected table because its top-level return code alone is weak.
            official=attempt/'official';official.mkdir();bin_dir=attempt/'bin';bin_dir.mkdir()
            wrapper=bin_dir/'klayout';wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(a.klayout)+' "$@"\n');wrapper.chmod(0o755)
            env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
            # The ORFS shell may carry OSSCAD's Python home; the PDK runner
            # uses its own Python environment with klayout.db and docopt.
            for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
                env.pop(key, None)
            runner=rule_dir.parent/'run_drc.py'
            cmd=[os.environ.get('R2G_GF180_DRC_PYTHON',sys.executable),str(runner),
                 '--path='+str(gds),'--variant=C','--topcell='+a.top,'--run_dir='+str(official),
                 '--mp=4','--thr=2','--antenna']
            rc=run(cmd,rule_dir.parent,attempt/'official.log',a.timeout,env)
            record.update(checker='gf180_official_run_drc',exit_code=rc,official_runner_sha256=sha(runner))
            if rc: raise ValueError('Official GF180 checker failed or timed out')
            report=attempt/'official_merged.lyrdb'
            record['tables_total']=merge_official(rule_dir,official,gds.stem,report)
            count=report_items(report)
        if sha(gds)!=digest:raise ValueError('GDS changed during signoff')
        shutil.copy2(report,canonical);(out/'6_drc_count.rpt').write_text(str(count)+'\n')
        record.update(status='clean' if count==0 else 'violations',violations=count,exit_code=0)
        print(f'DRC completed: {count} violations found')
    except Exception as exc:
        record.update(status='timeout' if record.get('exit_code')==124 else 'failed',reason=str(exc))
        print('GF180 DRC failed: '+str(exc),file=sys.stderr)
    finally:
        record.update(ended_at=time.strftime('%FT%T%z'),wall_s=round(time.time()-start,3))
        (out/'drc_result.json').write_text(json.dumps(record,indent=2)+'\n')
        # Keep the log older than newly produced artifacts for the existing extractor.
        log=attempt/('official.log' if (attempt/'official.log').exists() else 'fast.log')
        if log.exists():
            shutil.copy2(log,out/'drc_run.log');os.utime(out/'drc_run.log',(start,start))
    return 0 if record['status'] in ('clean','violations') else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,cancelled);signal.signal(signal.SIGINT,cancelled)
    raise SystemExit(main())
