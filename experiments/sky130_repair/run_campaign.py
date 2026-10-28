#!/usr/bin/env python3
"""Isolated SKY130 repair arm; retain baseline configuration and checkers.

Run on 216 with the adjacent frozen frontend/hooks bundle. No writes to the
baseline or original RTL. Each candidate records configuration delta and fresh
DRC/LVS; repair wins require both checks to be explicitly clean.
"""
import argparse
import concurrent.futures
import dataclasses
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from source_audit import audit_sources
from upstream_assets import restore_assets
from strict_signoff import inspect_run

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'agent'))
from r2g_agent import frontend

D=Path('/proj/workarea/yangao')
PACKAGE=D/'rtl-certified-handoff-0002'
IMAGE='localhost/r2g-orfs:26Q3'
TIMEOUT=21600
CORES=8
POLICY_REVISION=29
REVIEWED_NEW_CASES={'C00766','C01292','C01492','C02514','C02802','C03009','C01034','C01941','C01979','C02108','C02175','C02521','C02596','C03091','C03259','C03490','C03951','C03954','C03963','C03964','C03965','C04010','C04011'}


def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path); result=importlib.util.module_from_spec(spec)
    sys.modules[name]=result;spec.loader.exec_module(result);return result


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def mk(text,key):
    m=re.search(r'^export\s+'+re.escape(key)+r'\s*=\s*(.*)$',text,re.M)
    return m.group(1).strip() if m else ''


def set_mk(text,key,value):
    text=re.sub(r'^export\s+'+re.escape(key)+r'\s*=.*\n?', '', text, flags=re.M)
    return text.rstrip()+'\nexport '+key+' = '+value+'\n'


def queue_estimate(case):
    """Scheduling hint only; never infer physical size from an early failure.

    Completed physical runs give a useful relative cost. A failed frontend's
    few seconds and missing cell count do not describe the eventual layout.
    Every selected design remains queued, with the same execution limits.
    """
    seconds=case.get('seconds')
    if (case.get('status')=='pass' or case.get('stage','').startswith('6_')) and isinstance(seconds,(int,float)) and seconds>0:
        return float(seconds),'baseline_completed_physical_runtime'
    if case['category'].startswith('timeout_'):
        return float(TIMEOUT),'baseline_timeout_budget'
    return 3600.0,'unknown_physical_cost_one_hour_scheduling_placeholder'


def order_cases(cases, canary=False):
    priority={'C00303':0,'C02098':1,'C00241':2,'C02620':3}
    def key(case):
        cells=case.get('cells')
        size=cells if isinstance(cells,(int,float)) and cells>0 else math.inf
        first=priority.get(case['design'].split('__')[0],4) if canary else 0
        return first,queue_estimate(case)[0],size,case['design']
    return sorted(cases,key=key)


def pin_side(run):
    text=(run/'config.mk').read_text();die=[float(v) for v in mk(text,'DIE_AREA').split()]
    if len(die)!=4:
        with (run/'6_final.def').open() as handle:
            header=''.join(next(handle,'') for _ in range(20))
        units=re.search(r'UNITS DISTANCE MICRONS (\d+)',header)
        bounds=re.search(r'DIEAREA\s*\(\s*(\d+)\s+(\d+)\s*\)\s*\(\s*(\d+)\s+(\d+)\s*\)',header)
        if not units or not bounds:return None
        die=[float(v)/float(units.group(1)) for v in bounds.groups()]
    items=ET.parse(run/'6_drc.lyrdb').findall('.//items/item')
    sides=[]
    for item in items:
        if item.findtext('category','').strip("'") not in ('m3.1','m3.2'):return None
        values=' '.join(v.text or '' for v in item.findall('values/value'))
        pairs=[tuple(map(float,p)) for p in re.findall(r'(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)',values)]
        if not pairs:return None
        x=sum(p[0] for p in pairs)/len(pairs);y=sum(p[1] for p in pairs)/len(pairs)
        distances={'left':abs(x-die[0]),'right':abs(x-die[2]),'bottom':abs(y-die[1]),'top':abs(y-die[3])}
        side=min(distances,key=distances.get)
        if distances[side]>3:return None
        sides.append(side)
    return sides[0] if sides and len(set(sides))==1 else None


def verified_inactive_includes(work,baseline_cfg,sources):
    """Exact compiler witness, never a general missing-include exemption."""
    if mk(baseline_cfg,'DESIGN_NAME')!='itrx_aib_phy_io_buf_rx':return []
    evidence=json.loads((HERE/'c03954_inactive_include_evidence.json').read_text())
    if (hashlib.sha256(baseline_cfg.encode()).hexdigest()!=evidence['original_config_sha256']
            or [v['returncode'] for v in evidence['probes']]!=[0,1,1]):
        raise ValueError('conditional include configuration or compiler witness differs')
    # Source order is also pinned: Verilog preprocessor definitions are global.
    if ['/work/'+str(p.relative_to(work)) for p in sources]!=evidence['compiled_sources']:
        raise ValueError('conditional include compilation order differs')
    files={str(p.relative_to(work/'rtl')):sha(p) for p in (work/'rtl').rglob('*') if p.is_file()}
    if files!=evidence['source_sha256']:
        raise ValueError('conditional include source/header content differs')
    save(work/'conditional_include_evidence.json',evidence)
    return evidence['inactive_includes']


def source_repair(work, baseline_cfg, source_package=None):
    """Reuse the Agent's source fixes on private copies of the same RTL."""
    source_package=Path(source_package) if source_package is not None else PACKAGE
    logical_files=mk(baseline_cfg,'VERILOG_FILES').split()
    if any(not Path(p).is_relative_to(PACKAGE/'sources') for p in logical_files):
        raise ValueError('needs_explicit_source_mapping for non-package source')
    files=[str(source_package/Path(p).relative_to(PACKAGE)) for p in logical_files]
    roots=set()
    for filename in files:
        p=Path(filename)
        if not p.is_relative_to(source_package/'sources'):
            raise ValueError('needs_explicit_source_mapping: '+filename)
        roots.add(source_package/'sources'/p.relative_to(source_package/'sources').parts[0])
    if len(roots)!=1:raise ValueError('multiple source repositories need explicit mapping')
    root=roots.pop();rtl=work/'rtl';rtl.mkdir()
    copied=[]
    original_files={Path(f) for f in files}
    for f in root.rglob('*'):
        if not f.is_file() or (f not in original_files and f.suffix.lower() not in ('.v','.sv','.vh','.svh','.vi','.inc',*frontend.DATA_SUFFIXES)):continue
        if not f.resolve().is_relative_to(root.resolve()):continue
        target=rtl/f.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,target);copied.append(target)
    only={rtl/Path(f).relative_to(root) for f in files}
    restored,overrides,asset_manifest=restore_assets(HERE/'upstream_memory_assets',root,rtl,original_files)
    restored_fixes=[]
    if asset_manifest:
        save(work/'upstream_assets.json',asset_manifest)
        for f in copied:
            if f.suffix.lower() not in ('.v','.sv','.vh','.svh'):continue
            text=f.read_bytes();updated=text
            for ref,rel in overrides.items():
                updated=updated.replace(('"'+ref+'"').encode(),('"'+str(rtl/rel)+'"').encode())
            if updated!=text:
                f.write_bytes(updated);restored_fixes.append(dict(recipe='readmem_original_upstream',file=str(f.relative_to(rtl))))
    sources,fixes,missing=frontend.fix_sources(copied,root,rtl,only=only)
    fixes=restored_fixes+fixes
    # The certified manifest may include HDL fragments with nonstandard suffixes.
    sources += sorted(p for p in only if p.suffix.lower() not in ('.v','.sv','.vh','.svh') and p not in sources)
    for f in copied:
        if f.suffix.lower() in ('.v','.sv','.vh','.svh'):
            text=f.read_bytes();updated=text.replace(str(work).encode(),b'/work')
            if updated!=text:f.write_bytes(updated)
    if missing:raise ValueError('missing_or_ambiguous_data: '+json.dumps(missing,ensure_ascii=False))
    inactive=verified_inactive_includes(work,baseline_cfg,sources)
    audit=audit_sources(root,rtl,work,original_files,sources,frontend,
                        mk(baseline_cfg,'VERILOG_INCLUDE_DIRS').replace(str(PACKAGE),str(source_package)).split(),restored=restored,overrides=overrides,inactive_includes=inactive)
    save(work/'source_audit.json',audit)
    logical_root=PACKAGE/root.relative_to(source_package)
    cfg=baseline_cfg.replace(str(logical_root),'/work/rtl')
    cfg=set_mk(cfg,'VERILOG_FILES',' '.join('/work/'+str(p.relative_to(work)) for p in sources))
    return cfg,fixes


def order_reviewed_fpu_packages(work,name,baseline_cfg,cfg):
    evidence=json.loads((HERE/'fpu_package_order_evidence.json').read_text())['cases'].get(name.split('__')[0])
    if not evidence or evidence['design']!=name:raise ValueError('FPU package order is not reviewed for this design')
    if hashlib.sha256(baseline_cfg.encode()).hexdigest()!=evidence['config_sha256']:
        raise ValueError('FPU original configuration changed')
    if set(mk(cfg,'VERILOG_FILES').split())!={'/work/'+p for p in evidence['original_order']}:
        raise ValueError('FPU compilation source set changed')
    for rel,digest in evidence['source_sha256'].items():
        if sha(work/rel)!=digest:raise ValueError('FPU original source/package changed')
    if [r['returncode'] for r in evidence['local_original_and_reordered']]!=[1,0]:
        raise ValueError('FPU package-order validation absent')
    save(work/'source_order_evidence.json',evidence)
    return set_mk(cfg,'VERILOG_FILES',' '.join('/work/'+p for p in evidence['ordered_sources']))


def restore_source_aliases(work, name, cfg, driver):
    """Reconstruct the frozen driver's path aliases without changing its config."""
    if '/work/_src/' not in cfg:return
    design=PACKAGE/'certified'/name
    meta=json.loads((design/'DESIGN.json').read_text())
    files=driver._dedupe([str((design/f['file']).resolve()) for f in meta.get('source_files',[])])
    incs=driver._dedupe([str((design/i).resolve()) for i in meta.get('include_dirs',[])])
    files=driver._drop_included(driver._hdl_only(files),incs)
    if not all(Path(p).is_relative_to(PACKAGE/'sources') for p in files+incs):
        raise ValueError('source alias escapes the certified source package')
    links={}
    files=driver._make_safe(files,work,links);incs=driver._make_safe(incs,work,links)
    if files!=mk(cfg,'VERILOG_FILES').split() or incs!=mk(cfg,'VERILOG_INCLUDE_DIRS').split():
        raise ValueError('reconstructed source aliases differ from the frozen baseline')


def podman(work, tag, command, logfile, timeout=TIMEOUT):
    args=['podman','run','--rm','--name',tag,'--memory=64g','--memory-swap=64g',
          '-v',str(work)+':/work:Z','-v',str(PACKAGE)+':'+str(PACKAGE)+':ro,Z',
          '-e','NUM_CORES='+str(CORES),'-e','OMP_NUM_THREADS='+str(CORES),IMAGE,'bash','-lc',command]
    with logfile.open('w') as log:
        try:return subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            subprocess.run(['podman','rm','-f',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
            return 124


def translate(work, cfg, top, tag):
    args=['-D','SYNTHESIS','--keep-hierarchy','--compat=vcs','--ignore-assertions','--top',top]
    for inc in mk(cfg,'VERILOG_INCLUDE_DIRS').split():args+=['-I',inc]
    args+=mk(cfg,'VERILOG_DEFINES').split()+mk(cfg,'VERILOG_FILES').split()
    script='yosys plugin -i slang\nyosys read_slang '+' '.join(map(frontend._tcl_quote,args))+'\n'
    script+='yosys hierarchy -check -top '+frontend._tcl_quote(top)+'\nyosys proc\nyosys opt_clean\nyosys write_verilog -noattr /work/slang.v\n'
    (work/'slang.tcl').write_text(script)
    rc=podman(work,tag+'_sv','/r2g_toolchain/oss-cad-suite/bin/yosys -T -c /work/slang.tcl',work/'slang.log',1800)
    if rc or not (work/'slang.v').is_file():raise ValueError('slang_translation_failed: see slang.log')
    return set_mk(cfg,'VERILOG_FILES','/work/slang.v')


def translate_internal_tristate(work,cfg,top,tag):
    """Exact reviewed C02108 compiler conversion; preserve its original ROM."""
    evidence=json.loads((HERE/'c02108_tribuf_evidence.json').read_text())
    if top!='soc' or evidence['design']!='C02108__soc__udarkrisc' or mk(cfg,'VERILOG_DEFINES'):
        raise ValueError('internal-tristate conversion is only reviewed for the original SoC configuration')
    if [r['returncode'] for r in evidence['positive_and_negative_runs']]!=[0,1,0,1]:
        raise ValueError('internal-tristate positive or negative validation failed')
    sources=evidence['source_order']
    if set(mk(cfg,'VERILOG_FILES').split())!={'/work/'+p for p in sources}:
        raise ValueError('internal-tristate compilation source set differs')
    for filename,digest in evidence['source_sha256'].items():
        if sha(work/filename)!=digest:raise ValueError('internal-tristate original source or ROM changed')
    script='read_verilog '+' '.join(json.dumps('/work/'+p) for p in sources)+'\n'
    script+='hierarchy -check -top soc\nproc\nflatten\ntribuf -logic\nopt\nhierarchy -check -top soc\nwrite_verilog -noattr /work/frontend.v\n'
    (work/'frontend.ys').write_text(script)
    rc=podman(work,tag+'_tribuf','yosys -Q -T -s /work/frontend.ys',work/'frontend.log',1800)
    if rc or not (work/'frontend.v').is_file():raise ValueError('internal-tristate compiler conversion failed')
    if sha(work/'frontend.v')!=evidence['actual_translation_sha256']:
        raise ValueError('internal-tristate actual translation differs from the tested netlist')
    save(work/'translation_evidence.json',evidence)
    return set_mk(cfg,'VERILOG_FILES','/work/frontend.v')


def reviewed_source_congestion_retry(case,previous):
    if (not case['design'].startswith('C01034__') or previous.get('status')!='not_clean'
            or previous.get('policy_revision',1)>=14 or not previous.get('attempts')):return False
    last=previous['attempts'][-1]
    if last.get('strategy')!='source_normalization' or last.get('failure_class')!='route_failed':return False
    work=Path(last['work']);audit=work/'source_audit.json'
    if not audit.is_file() or json.loads(audit.read_text()).get('status')!='source_preservation_checked':return False
    if sha(audit)!=last.get('implementation_sha256',{}).get('source_audit.json'):raise ValueError('reviewed source evidence changed')
    with (work/'flow.log').open('rb') as f:
        f.seek(max(0,(work/'flow.log').stat().st_size-65536));tail=f.read().decode(errors='replace')
    return '[ERROR GRT-0116] Global routing finished with congestion' in tail


def reviewed_frontend_placement_retry(case,previous):
    """Retry only the diagnosed, proof-bound PLIC density failure."""
    if (not case['design'].startswith('C03966__') or previous.get('status')!='not_clean'
            or previous.get('policy_revision',1)>=15 or not previous.get('attempts')):return False
    last=previous['attempts'][-1]
    if last.get('strategy')!='sv_frontend' or last.get('status')!='fail':return False
    work=Path(last['work']);proof=work/'translation_evidence.json'
    if not proof.is_file():return False
    if sha(proof)!=last.get('implementation_sha256',{}).get('translation_evidence.json'):
        raise ValueError('reviewed translation evidence changed')
    evidence=json.loads(proof.read_text())
    if sha(work/'slang.v')!=evidence['sha256']['slang.v']:
        raise ValueError('reviewed translation changed')
    with (work/'flow.log').open('rb') as f:
        f.seek(max(0,(work/'flow.log').stat().st_size-65536));tail=f.read().decode(errors='replace')
    density=re.findall(r'\[INFO DPL-0006\].*Utilization:\s*([\d.]+)%',tail)
    return '[ERROR DPL-0036]' in tail and bool(density) and float(density[-1])>=90


def reviewed_source_pdn_retry(case,previous):
    if (not case['design'].startswith('C03954__') or previous.get('status')!='not_clean'
            or previous.get('policy_revision',1)>=18 or not previous.get('attempts')):return False
    last=previous['attempts'][-1]
    if last.get('strategy')!='source_normalization' or last.get('status')!='fail':return False
    work=Path(last['work'])
    for name in ('source_audit.json','conditional_include_evidence.json'):
        if sha(work/name)!=last.get('implementation_sha256',{}).get(name):
            raise ValueError('reviewed PHY source evidence changed')
    with (work/'flow.log').open('rb') as handle:
        handle.seek(max(0,(work/'flow.log').stat().st_size-8192));tail=handle.read().decode(errors='replace')
    return ('[ERROR PDN-0185] Insufficient width (12.42 um)' in tail
            and 'layer met4' in tail and 'total strap width 15.2 um and offset 13.6 um' in tail)


def reviewed_source_pin_retry(case,previous):
    """Only retry the completed, source-preserving filter's diagnosed PDN gap."""
    if (case['design']!='C03391__q_lpf_decim_axi__2026_g_periodic-signal-measurement-and-a'
            or previous.get('status')!='not_clean' or not previous.get('attempts')):return False
    last=previous['attempts'][-1]
    if last.get('strategy')!='source_normalization' or last.get('status')!='completed':return False
    evidence=json.loads((HERE/'c03391_pin_geometry_evidence.json').read_text())
    if (evidence['design']!=case['design'] or last['work']!=evidence['prior_work']
            or last['run']!=evidence['prior_run']):return False
    for path,digest in evidence['sha256'].items():
        if sha(Path(path))!=digest:raise ValueError('reviewed filter geometry/source evidence changed')
    audit=evidence['source_audit']
    if audit['status']!='source_preservation_checked':return False
    for entry in audit['files']:
        original=Path(entry['path'])
        private=Path(evidence['private_root'])/original.relative_to(evidence['original_root'])
        if sha(original)!=entry['original_sha256'] or sha(private)!=entry['private_sha256']:
            raise ValueError('reviewed filter RTL changed')
    signed=inspect_run(Path(last['run']))
    # inspect_run counts DRC items only for claimed-clean reports. Here the
    # original report explicitly has violations; inspect its pinned XML too.
    items=ET.parse(Path(last['run'])/'6_drc.lyrdb').findall('.//items/item')
    return (signed['lvs']=='clean' and not signed['dual_clean'] and len(items)==2
            and all((item.findtext('category') or '').strip("'\"")=='m3.2' for item in items))


def reviewed_area_feedthrough_retry(case,previous):
    if (case['design']!='C00435__b2bcd__verilog_fundamentals'
            or previous.get('status')!='not_clean' or previous.get('policy_revision',1)>=20
            or len(previous.get('attempts',[]))!=2):return False
    attempts=previous['attempts']
    if ([a.get('strategy') for a in attempts]!=['area_expansion_1.25','area_expansion_1.50']
            or any(a.get('status')!='completed' for a in attempts)):return False
    evidence=json.loads((HERE/'c00435_compound_evidence.json').read_text())
    first=attempts[0]
    if (evidence['design']!=case['design'] or first['work']!=evidence['prior_work']
            or first['run']!=evidence['prior_run']):return False
    for path,digest in evidence['sha256'].items():
        if sha(Path(path))!=digest:raise ValueError('reviewed area/feedthrough evidence changed')
    run=Path(first['run']);netlist=(run/'6_final.v').read_text()
    if re.search(r'\binout\b',netlist) or not re.search(r'assign\s+bcd\[0\]\s*=\s*b\[0\]\s*;',netlist):return False
    signed=inspect_run(run)
    return signed['drc']=='clean' and signed['lvs']=='pin_mismatch'


def reviewed_i2c_driver_retry(case,previous):
    if (case['design']!='C03259__I2C_module__Serial-Communication-Protocols'
            or previous.get('policy_revision') not in (24,25,26) or previous.get('status')!='not_clean'
            or len(previous.get('attempts',[]))!=1):return False
    last=previous['attempts'][-1]
    if last.get('strategy')!='preserve_i2c_tristate_hierarchy':return False
    if last.get('status')!=('completed' if previous['policy_revision']==26 else 'fail'):return False
    witness=json.loads((HERE/'c03259_hierarchy_evidence_v27.json').read_text())
    evidence=witness[{24:'prior_failed_cleanup',25:'prior_failed_shared_bus',26:'prior_failed_power'}[previous['policy_revision']]]
    work=Path(last['work'])
    if str(work)!=evidence['work']:return False
    for name,digest in evidence['files'].items():
        if sha(work/name)!=digest:raise ValueError('I2C failed-cleanup diagnosis artifacts changed')
    if previous['policy_revision']==26:
        run=Path(last['run'])
        if str(run)!=evidence['run'] or last.get('drc')!='clean' or last.get('lvs')!='mismatch':return False
        for name,digest in evidence['run_files'].items():
            if sha(run/name)!=digest:raise ValueError('I2C power-failure signoff evidence changed')
        return inspect_run(run)['lvs']=='mismatch'
    with (work/'flow.log').open('rb') as handle:
        handle.seek(max(0,(work/'flow.log').stat().st_size-8192));tail=handle.read().decode(errors='replace')
    return ('I2C SDA must retain both original independently controlled tri-state drivers' in tail
            if previous['policy_revision']==24 else '[ERROR RSZ-0074]' in tail and "net 'sda'" in tail)


def prepare_i2c_hierarchy(work,name,cfg,source_package=None):
    """Exact reviewed original-source compilation; retain both real tri-states."""
    evidence=json.loads((HERE/'c03259_hierarchy_evidence_v27.json').read_text())
    if (name!=evidence['design'] or hashlib.sha256(cfg.encode()).hexdigest()!=evidence['baseline_config_sha256']
            or sha(work/'constraint.sdc')!=evidence['original_sdc_sha256']):
        raise ValueError('I2C candidate requires exact original design, configuration and SDC')
    if any(mk(cfg,k) for k in ('SYNTH_KEEP_MODULES','PRE_SYNTH_TCL','PRE_GLOBAL_ROUTE_TCL','PRE_PDN_TCL','PRE_FINAL_REPORT_TCL','SYNTH_GUT','SYNTH_MOCK_LARGE_MEMORIES')):
        raise ValueError('I2C candidate has an unreviewed synthesis/final hook setting')
    package=Path(source_package) if source_package is not None else PACKAGE
    for rel,digest in evidence['original_source_sha256'].items():
        path=package/rel
        if not path.resolve().is_relative_to(package/'sources') or sha(path)!=digest:
            raise ValueError('I2C original source changed')
    if (evidence['compiler']['returncode']!=0 or evidence['compiler']['check_disabled']
            or not evidence['compiler']['negative_conflict']['rejected']
            or evidence['frozen_synthesis']['returncode']!=0):
        raise ValueError('I2C original compiler checks not demonstrated')
    for traffic in evidence['traffic']:
        if (traffic['results']['positive']['returncode']!=0
                or 'PASS checks=65664' not in traffic['results']['positive']['log_tail']
                or traffic['results']['negative']['returncode']!=1
                or 'MISMATCH' not in traffic['results']['negative']['log_tail']):
            raise ValueError('I2C bounded original-RTL traffic or master negative control failed')
    if not evidence['slave_negative']['detected'] or evidence['slave_negative']['returncode']!=1:
        raise ValueError('I2C slave driver negative control missing')
    if {k:v['returncode'] for k,v in evidence['database_preflight']['results'].items()}!={
            'positive':0,'missing_slave':1,'wrong_direction':1}:
        raise ValueError('I2C final database topology guard preflight failed')
    cleanup=evidence['dead_logic_preservation'];traffic=evidence['post_cleanup_traffic']['results']
    if (cleanup['results']['control']['returncode']!=1 or cleanup['results']['preserved']['returncode']!=0
            or traffic['positive']['returncode']!=0 or 'PASS checks=65664' not in traffic['positive']['log']
            or traffic['negative']['returncode']!=1 or 'MISMATCH' not in traffic['negative']['log']
            or cleanup['preservation_hook_sha256']!=evidence['candidate_files_sha256']['preserve_i2c_tristate_drivers.tcl']):
        raise ValueError('I2C dead-logic preservation lacks actual cleanup and behavioral controls')
    bus=evidence['shared_bus_preservation']['results'];route_traffic=evidence['post_route_traffic']['results']
    if (bus['control']['returncode']!=1 or 'RSZ-0074' not in bus['control']['tail']
            or bus['preserved']['returncode']!=0 or 'R2G_SHARED_BUS_REPAIR_PASS' not in bus['preserved']['tail']
            or route_traffic['positive']['returncode']!=0 or 'PASS checks=65664' not in route_traffic['positive']['log']
            or route_traffic['negative']['returncode']!=1 or 'MISMATCH' not in route_traffic['negative']['log']):
        raise ValueError('I2C shared-bus preservation lacks routing and behavioral controls')
    power=evidence['power_hook_preflight'];guard=evidence['final_power_guard']
    if (any(x['returncode']!=0 or 'R2G_ALL_SIGNAL_CONNECTIONS_UNCHANGED' not in x['log'] for x in power['results'].values())
            or set(power['results'])!={'2_3_floorplan_tapcell','6_final'}
            or guard['results']['positive']['returncode']!=0
            or guard['results']['missing_power_negative']['returncode']!=1
            or power['hook_sha256']!=evidence['candidate_files_sha256']['connect_i2c_driver_power.tcl']
            or guard['hook_sha256']!=evidence['candidate_files_sha256']['check_i2c_final_power.tcl']):
        raise ValueError('I2C power hookup lacks connectivity preservation or final negative control')
    artifacts=evidence['candidate_files_sha256']
    if (artifacts['c03259_hierarchical_mapped.v']!=evidence['traffic'][0]['mapped_sha256']
            or artifacts['c03259_hierarchical_mapped.v']!=evidence['frozen_synthesis']['source_candidate_sha256']
            or evidence['traffic'][1]['mapped_sha256']!=evidence['frozen_synthesis']['frozen_flow_synthesized_sha256']
            or artifacts['check_i2c_tristate_topology.tcl']!=evidence['database_preflight']['hook_sha256']):
        raise ValueError('I2C evidence is not bound to the actual candidate artifacts')
    for filename,digest in artifacts.items():
        if sha(HERE/filename)!=digest:raise ValueError('I2C candidate differs from reviewed artifact: '+filename)
    for filename in artifacts:shutil.copy2(HERE/filename,work/filename)
    rtl=work/'rtl';rtl.mkdir()
    for rel in evidence['original_source_sha256']:shutil.copy2(package/rel,rtl/Path(rel).name)
    save(work/'i2c_hierarchy_evidence.json',evidence)
    cfg=set_mk(set_mk(cfg,'VERILOG_FILES','/work/c03259_hierarchical_mapped.v'),'VERILOG_INCLUDE_DIRS','/work')
    cfg=set_mk(cfg,'SYNTH_KEEP_MODULES','*I2C_master* *I2C_slave*')
    cfg=set_mk(cfg,'PRE_SYNTH_TCL','/work/preserve_i2c_tristate_drivers.tcl')
    cfg=set_mk(cfg,'PRE_GLOBAL_ROUTE_TCL','/work/preserve_i2c_shared_bus.tcl')
    cfg=set_mk(cfg,'PRE_PDN_TCL','/work/connect_i2c_driver_power.tcl')
    return set_mk(cfg,'PRE_FINAL_REPORT_TCL','/work/check_i2c_final_power.tcl')


def validate_i2c_final_netlist(path,flow_log):
    """Conservative final serialization check, in addition to the OpenDB guard."""
    if any(marker not in Path(flow_log).read_text(errors='replace') for marker in ('R2G_I2C_FINAL_TOPOLOGY_PASS','R2G_I2C_FINAL_POWER_PASS')):
        raise ValueError('I2C final database connectivity guard did not execute')
    text=re.sub(r'/\*.*?\*/|//[^\n]*','',Path(path).read_text(),flags=re.S)
    if len(re.findall(r'\bmodule\b',text))!=1 or not re.search(r'\binout\s+sda\s*;',text):
        raise ValueError('I2C final SDA interface or netlist representation changed')
    cells=re.findall(r'\bsky130_fd_sc_hd__ebufn_1\s+(\S+)\s*\((.*?)\);',text,re.S)
    if len(cells)!=2 or len(re.findall(r'\.Z\(\s*sda\s*\)',text))!=2:
        raise ValueError('I2C final netlist must retain both real SDA tri-state drivers')
    for name,ports in cells:
        if not re.search(r'\.Z\(\s*sda\s*\)',ports):raise ValueError('I2C tri-state no longer drives SDA')
        for pin in ('A','TE_B'):
            match=re.search(r'\.'+pin+r'\(\s*([^)]*?)\s*\)',ports)
            if not match or not re.fullmatch(r'(?:\\\S+|[A-Za-z_]\w*)',match[1]) or match[1]=='sda':
                raise ValueError('I2C tri-state data/enable tied or changed representation')
    if any(re.search(r'\bsda\b',a) for a in re.findall(r'\bassign\b(.*?);',text,re.S)):
        raise ValueError('I2C final SDA alias needs explicit review')
    return dict(netlist_sha256=sha(path),drivers=[name for name,_ in cells],sda_inout=True,
                database_guard_passed=True,scope='Real final tri-state topology plus pre-physical bounded traffic; not unbounded equivalence')


def prepare_zube_driver_replication(work,name,cfg,source_package=None):
    evidence=json.loads((HERE/'c03490_driver_replication_evidence.json').read_text())
    if name!=evidence['design'] or hashlib.sha256(cfg.encode()).hexdigest()!=evidence['baseline_config_sha256']:
        raise ValueError('inout driver candidate requires exact reviewed original configuration')
    if mk(cfg,'POST_GLOBAL_PLACE_TCL'):raise ValueError('existing placement hook needs explicit composition review')
    package=Path(source_package) if source_package is not None else PACKAGE
    for rel,digest in evidence['original_source_sha256'].items():
        path=package/rel
        if not path.resolve().is_relative_to(package/'sources') or sha(path)!=digest:
            raise ValueError('reviewed original inout RTL changed')
    proof=evidence['cone_proof'];preflight=evidence['database_preflight']
    if (not proof['local_cone_exhaustive'] or proof['checks']!=262144 or proof['negative_mismatches']!=65024
            or not preflight['all_port_directions_unchanged'] or preflight['added_inverters']!=7
            or not preflight['idempotent'] or not all(v['rejected'] for v in preflight['negative_controls'].values())):
        raise ValueError('inout candidate lacks required cone and topology evidence')
    hook=HERE/'replicate_zube_inout_drivers.tcl'
    if sha(hook)!=evidence['hook_sha256'] or sha(hook)!=preflight['hook_sha256']:
        raise ValueError('inout driver hook differs from reviewed implementation')
    shutil.copy2(hook,work/hook.name);save(work/'driver_replication_evidence.json',evidence)
    return set_mk(cfg,'POST_GLOBAL_PLACE_TCL','/work/'+hook.name)


def validate_zube_final_netlist(path):
    """Require the proven cone to survive every later optimization stage."""
    text=re.sub(r'/\*.*?\*/|//[^\n]*','',Path(path).read_text(),flags=re.S)
    for port in ('io_oeb','io_out'):
        if not re.search(r'\binout\s+\[27:0\]\s+'+port+r'\s*;',text):
            raise ValueError('final inout interface changed or uses an unreviewed representation')
    drivers=[]
    cells=re.findall(r'\bsky130_fd_sc_hd__inv_1\s+(\S+)\s*\((.*?)\);',text,re.S)
    for bit in range(8,16):
        target=r'io_oeb\['+str(bit)+r'\]'
        if len(re.findall(target,text))!=1:
            raise ValueError('final output-enable pin has aliases or additional internal connections')
        matched=[name for name,ports in cells if re.search(r'\.Y\(\s*'+target+r'\s*\)',ports)
                 and re.search(r'\.A\(\s*io_out\[16\]\s*\)',ports)]
        if len(matched)!=1:raise ValueError('final driver no longer reads the original resolved control')
        drivers.extend(matched)
    if len(set(drivers))!=8 or len(re.findall(r'io_out\[16\]',text))!=9:
        raise ValueError('final control connectivity differs from the proven local cone')
    return dict(netlist_sha256=sha(path),independent_inout_drivers=8,drivers=drivers,
                original_resolved_control='io_out[16]',inout_interfaces_preserved=True,
                scope='Final mapped topology matches the exhaustively checked original local drive cone; full-chip/timing equivalence not claimed')


def run_case(case,out,driver,drc,lvs,seed=None):
    baseline=Path(case['run']);name=case['design'];run_name=baseline.name
    imported=out.parent/'verified_imports/cases'/name/'repair.json'
    if imported.exists():
        verified=json.loads(imported.read_text())
        if (verified['design']!=name or verified['baseline']!=str(baseline)
                or not all(sha(baseline/k)==v for k,v in verified['baseline_hashes'].items())
                or not inspect_run(Path(verified['attempts'][-1]['run']))['dual_clean']):
            raise ValueError('imported result no longer matches this frozen design or strict signoff')
        return dict(verified,reused_from=str(imported))
    target=out/'cases'/name;receipt=target/'repair.json'
    placement_retry=False;source_congestion_retry=False;source_pdn_retry=False;source_pin_retry=False;area_feedthrough_retry=False;archive=None
    if receipt.exists():
        previous=json.loads(receipt.read_text())
        if previous.get('status')=='dual_clean':
            audited=inspect_run(Path(previous['attempts'][-1]['run']))
            if not audited['dual_clean']:
                archive=target/'repair_before_strict_signoff.json'
                if not archive.exists():save(archive,previous)
                previous.update(status='not_clean',acceptance_audit=audited,
                    reason='Historical checker status conflicts with top-level final evidence; not a repair win')
                save(receipt,previous)
                return previous
        if (name.startswith('C02511__') and previous['status']=='not_clean'
                and previous.get('policy_revision',1)<4):
            last=previous['attempts'][-1];logfile=Path(last['work'])/'flow.log'
            with logfile.open('rb') as handle:
                handle.seek(max(0,logfile.stat().st_size-65536));tail=handle.read().decode(errors='replace')
            placement_retry=(last['strategy']=='sv_frontend' and last.get('failure_class')=='place_failed'
                             and '[ERROR PPL-0024]' in tail and 'IO pins (1703)' in tail)
        placement_retry=placement_retry or reviewed_frontend_placement_retry(case,previous)
        source_congestion_retry=reviewed_source_congestion_retry(case,previous)
        source_pdn_retry=reviewed_source_pdn_retry(case,previous)
        source_pin_retry=reviewed_source_pin_retry(case,previous)
        area_feedthrough_retry=reviewed_area_feedthrough_retry(case,previous)
        reviewed_retry=placement_retry or source_congestion_retry or source_pdn_retry or source_pin_retry or area_feedthrough_retry or reviewed_i2c_driver_retry(case,previous)
        if not reviewed_retry and not (previous['status']=='blocked' and name.split('__')[0] in REVIEWED_NEW_CASES
                and previous.get('policy_revision',1)<POLICY_REVISION):return previous
        # Preserve old blocked diagnoses and the explicitly diagnosed TPU
        # placement failure before applying a newly supported strategy.
        if not reviewed_retry and previous['attempts']:raise ValueError('blocked case unexpectedly contains flow attempts')
        assert previous['design']==name and previous['baseline']==str(baseline)
        assert all(sha(baseline/k)==v for k,v in previous['baseline_hashes'].items())
        archive=target/('repair_before_policy_v'+str(POLICY_REVISION)+'.json')
        if not archive.exists():save(archive,previous)
        if json.loads(archive.read_text())!=previous:raise ValueError('retry archive differs from the existing receipt')
        receipt.unlink()  # The archived result must not hide the active retry.
    prior=seed/'cases'/name/'repair.json' if seed else None
    prior_failed_strategies=set()
    if prior and prior.is_file():
        record=json.loads(prior.read_text())
        if record['status']=='dual_clean' and record['design']==name and record['baseline']==str(baseline):
            assert all(sha(baseline/k)==v for k,v in record['baseline_hashes'].items())
            signed=json.loads((Path(record['attempts'][-1]['run'])/'result.json').read_text())
            audited=inspect_run(Path(record['attempts'][-1]['run']))
            if audited['dual_clean']:
                record['acceptance_audit']=audited
                record['reused_from']=str(prior);save(receipt,record);return record
        if record['design']==name and record['baseline']==str(baseline) and case['category']=='drc_violation':
            assert all(sha(baseline/k)==v for k,v in record['baseline_hashes'].items())
            prior_failed_strategies={a['strategy'] for a in record['attempts'] if a.get('run')}
    target.mkdir(parents=True,exist_ok=True)
    record=dict(design=name,category=case['category'],baseline=str(baseline),status='preparing',
                policy_revision=POLICY_REVISION,
                before={'drc':case['drc'],'lvs':case['lvs']},baseline_hashes={k:sha(baseline/k) for k in ('result.json','config.mk','constraint.sdc')},attempts=[])
    if archive:record['prior_receipt']=dict(path=str(archive),sha256=sha(archive))
    save(target/'progress.json',record)
    category=case['category'];tag='s130repair_'+name.split('__')[0]+'_'+str(os.getpid())
    native_lock=None;native_project=None
    try:
        basecfg=(baseline/'config.mk').read_text();top=mk(basecfg,'DESIGN_NAME')
        if name.startswith('C01292__'):
            raise ValueError('header repair is source-preserving, but frozen ch04 has empty FILE_FONT/FILE_PAL/FILE_TXT and tied-off writes; meaningful text-mode data needs an original intended configuration, not invented ROM contents')
        if name.startswith('C03951__'):
            raise ValueError('Slang avoids the frozen frontend exception, but original constant-only always @* dither has no simulation event; four-state original/translated outputs differ at cycle 5. Preserve registered output-enable/high-Z behavior; no PHY=NONE shortcut or unreviewed constant substitution')
        strategies=[]
        errors='\n'.join(case.get('errors',[]))
        if name.startswith('C00766__') and category=='timeout_1' and case['stage']=='1_2_yosys':
            logfile=baseline/'flow.log'
            with logfile.open('rb') as f:
                f.seek(max(0,logfile.stat().st_size-65536));tail=f.read().decode(errors='replace')
            if 'Extracting gate netlist of module' not in tail or 'Executing ABC pass' not in tail:
                raise ValueError('RAM timeout does not match the diagnosed ABC extraction stage')
            strategies=['retain_ram_bank_hierarchy']
        elif name.startswith('C02514__') and 'STA-1670' in errors and '6_final.spef' in errors:
            strategies=['spef_name_encoding']
        elif name.startswith('C02802__') and '131072 exceeds SYNTH_MEMORY_MAX_BITS' in errors:
            strategies=['memory_guard_262144']
        elif category=='backend_other' and 'RSZ-0074' in errors:
            strategies=['skip_incremental_repair']
        elif category=='memory_exhaustion' and case['stage']=='2_1_floorplan':
            logfile=baseline/'flow.log'
            with logfile.open('rb') as f:
                f.seek(max(0,logfile.stat().st_size-65536));tail=f.read().decode(errors='replace')
            if 'repair_timing -setup_margin' not in tail or 'out of memory' not in tail:
                raise ValueError('OOM does not match diagnosed floorplan timing optimizer')
            strategies=['floorplan_remove_abc_buffers']
        elif category in ('timeout_1','timeout_4'):
            raise ValueError('synthesis/tiny CTS timeout requires a stage-specific strategy; area expansion is not justified')
        elif name=='C03490__zube_wrapper__zube' and category=='lvs_pin_mismatch':
            strategies=['replicate_original_inout_drivers']
        elif category=='lvs_pin_mismatch':
            netlist=baseline/'6_final.v'
            if not netlist.is_file():raise ValueError('port-buffer candidate needs the original mapped port-direction evidence')
            if re.search(r'\binout\b',netlist.read_text(errors='replace')):
                raise ValueError('bidirectional port present: unidirectional feedthrough buffering requires separate semantic review')
            strategies=['lvs_port_feedthrough_buffer']
        elif category=='drc_violation':
            side=pin_side(baseline)
            if not side:raise ValueError('DRC not confined to a single die edge; needs geometry-specific repair')
            strategies=['pin_pdn_clearance','pin_side_rebalance:'+side]
        elif name=='C03259__I2C_module__Serial-Communication-Protocols' and category=='frontend_other':
            strategies=['preserve_i2c_tristate_hierarchy']
        elif name.startswith('C02108__') and category=='frontend_other' and "Found 48 problems in 'check -assert'" in errors:
            strategies=['internal_tristate_logic']
        elif (name.split('__')[0] in ('C03963','C03964','C03965') and category=='frontend_systemverilog'
                and 'Failed to detect width for parameter \\C_OP' in errors):
            strategies=['source_package_order']
        elif category.startswith('frontend'):
            strategies=['source_normalization']
            if name.split('__')[0] in ('C04010','C04011'):
                # Original A_WIDTH=14 and 32-bit words: 524288 bits. The
                # recovered original data exposes this previously masked
                # resource guard; preserve the complete RAM and its contents.
                strategies=['source_normalization_memory_guard_524288']
            if category=='frontend_async_load_flop':
                raise ValueError('async-load conversion requires original-RTL equivalence; C01169 and C03509 both have executable reset-behavior counterexamples')
            elif name.startswith('C02098'):strategies=['strip_vendor_attr']
            elif category in ('frontend_systemverilog','frontend_parse_review') and case['stage'].startswith('1_1'):
                strategies=['sv_frontend']
            elif category=='frontend_other':raise ValueError('frontend failure needs semantic review; no automatic RTL invention')
        elif category in ('routing_congestion','placement_capacity') or category.startswith('timeout_') or category=='memory_exhaustion':
            strategies=['area_expansion_1.25','area_expansion_1.50']
        else:raise ValueError('requires separate diagnosis; baseline evidence preserved')
        if placement_retry:strategies=['sv_frontend_area_expansion_1.25','sv_frontend_area_expansion_1.50']
        if source_congestion_retry:strategies=['source_normalization_area_expansion_1.25','source_normalization_area_expansion_1.50']
        if source_pdn_retry:strategies=['source_normalization_core_floor_32']
        normal_trials=out.parent/'normal_recipe_replays'/name.split('__')[0]/'normal_replay'
        if source_pin_retry:
            strategies=['normal_recipe_pin_pdn_clearance' if (normal_trials/'plan.json').is_file()
                        else 'source_normalization_pin_pdn_clearance']
        if area_feedthrough_retry:strategies=['area_expansion_1.25_port_buffer']
        if prior_failed_strategies:
            record['prior_failed_strategies']=sorted(prior_failed_strategies)
            record['prior_failed_receipt']=str(prior)
            strategies=[s for s in strategies if s not in prior_failed_strategies]
        for strategy in strategies:
            number=max([int(p.name.split('_')[-1]) for p in target.glob('attempt_*') if p.name.split('_')[-1].isdigit()]+[0])+1
            attempt=target/('attempt_'+str(number));work=attempt/'work';work.mkdir(parents=True)
            shutil.copy2(baseline/'constraint.sdc',work/'constraint.sdc');cfg=basecfg;fixes=[]
            restore_source_aliases(work,name,cfg,driver)
            if strategy=='normal_recipe_pin_pdn_clearance':
                from native_normal_replay import materialize, validate_execution, record as record_native
                cfg,native_lock,native_project=materialize(normal_trials,previous,PACKAGE,work)
            is_sv=strategy=='sv_frontend' or strategy.startswith('sv_frontend_area_expansion_')
            if strategy in ('source_normalization','source_normalization_memory_guard_524288','source_normalization_core_floor_32','source_normalization_pin_pdn_clearance','source_package_order','aldff_to_dffsr','strip_vendor_attr','internal_tristate_logic') or is_sv or strategy.startswith('source_normalization_area_expansion_'):
                cfg,fixes=source_repair(work,cfg)
                if strategy=='source_package_order':cfg=order_reviewed_fpu_packages(work,name,basecfg,cfg)
                if strategy=='internal_tristate_logic':cfg=translate_internal_tristate(work,cfg,top,tag)
                if strategy=='source_normalization_memory_guard_524288':
                    if mk(cfg,'SYNTH_MEMORY_MAX_BITS')!='131072':raise ValueError('unexpected original memory guard')
                    if mk(cfg,'SYNTH_MOCK_LARGE_MEMORIES') not in ('','0'):raise ValueError('mocked memory is not an acceptable repair')
                    cfg=set_mk(cfg,'SYNTH_MEMORY_MAX_BITS','524288')
                if strategy in ('aldff_to_dffsr','strip_vendor_attr'):
                    shutil.copy2(HERE/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/(strategy+'.tcl'),work/'frontend_fix.tcl')
                    cfg=set_mk(cfg,'SYNTH_CANONICALIZE_TCL','/work/frontend_fix.tcl')
                if is_sv:
                    cfg=translate(work,cfg,top,tag)
                    if name.startswith('C03980__'):
                        evidence=json.loads((HERE/'c03980_translation_evidence.json').read_text())
                        positive,negative=evidence['results']
                        if (positive['build_returncode'] or negative['build_returncode'] or
                                positive['simulation_returncode'] or negative['simulation_returncode']==0 or
                                evidence['negative_control_detected'] is not True):
                            raise ValueError('traffic comparison or active-data negative control failed')
                        for filename,digest in {**evidence['source_sha256'],'slang.v':evidence['sha256']['slang.v']}.items():
                            if sha(work/filename)!=digest:
                                raise ValueError('source/translation differs from the reviewed traffic simulation: '+filename)
                        save(work/'translation_evidence.json',evidence)
                    if name.split('__')[0] in ('C03962','C03986'):
                        evidence=json.loads((HERE/(name.split('__')[0].lower()+'_translation_evidence.json')).read_text())
                        if (evidence['results'][0]['returncode']!=0 or
                                evidence['results'][0]['equivalence_cells']<=0 or
                                evidence['results'][1]['returncode']==0):
                            raise ValueError('independent-frontend proof or negative control failed')
                        for filename,digest in {**evidence['source_sha256'],'slang.v':evidence['sha256']['slang.v']}.items():
                            if sha(work/filename)!=digest:
                                raise ValueError('source/translation differs from the reviewed independent-frontend proof: '+filename)
                        save(work/'translation_evidence.json',evidence)
                    if name.startswith('C03966__'):
                        evidence=json.loads((HERE/'c03966_translation_evidence.json').read_text())
                        if (evidence['results'][0]['returncode']!=0 or
                                evidence['results'][0]['proven_outputs']!=7 or
                                evidence['results'][1]['returncode']==0):
                            raise ValueError('C03966 translation proof or negative control failed')
                        if (sha(work/'rtl/rtl/rv_plic_target.sv')!=evidence['source_sha256'] or
                                sha(work/'slang.v')!=evidence['sha256']['slang.v']):
                            raise ValueError('C03966 source/translation differs from the reviewed independent-frontend proof')
                        save(work/'translation_evidence.json',evidence)
                    if placement_retry and name.startswith('C02511__'):
                        evidence=json.loads((HERE/'c02511_translation_evidence.json').read_text())
                        assert evidence['results'][0]['returncode']==0 and evidence['results'][1]['returncode']!=0
                        for filename,digest in evidence['sha256'].items():
                            if filename=='slang.v' or filename.startswith('rtl/'):
                                if sha(work/filename)!=digest:raise ValueError('translation/source differs from reviewed differential simulation: '+filename)
                        save(work/'translation_evidence.json',evidence)
                if strategy=='source_normalization' and not fixes:raise ValueError('no applicable source fix')
            elif strategy=='preserve_i2c_tristate_hierarchy':
                cfg=prepare_i2c_hierarchy(work,name,cfg)
            elif strategy=='replicate_original_inout_drivers':
                cfg=prepare_zube_driver_replication(work,name,cfg)
            elif strategy=='lvs_port_feedthrough_buffer':
                shutil.copy2(HERE/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl',work/'feedthrough.tcl')
                cfg=set_mk(cfg,'POST_GLOBAL_PLACE_TCL','/work/feedthrough.tcl')
            elif strategy.startswith('pin_side_rebalance:'):
                cfg=set_mk(cfg,'PLACE_PINS_ARGS','-exclude '+strategy.split(':')[1]+':*')
            elif strategy=='pin_pdn_clearance':
                shutil.copy2(HERE/'pin_pdn_clearance.tcl',work/'pin_pdn_clearance.tcl')
                cfg=set_mk(cfg,'PRE_IO_PLACEMENT_TCL','/work/pin_pdn_clearance.tcl')
            elif strategy=='spef_name_encoding':
                for filename in ('spef_name_encoding.tcl','normalize_spef_names.py'):
                    shutil.copy2(HERE/filename,work/filename)
                cfg=set_mk(cfg,'PRE_FINAL_REPORT_TCL','/work/spef_name_encoding.tcl')
            elif strategy=='memory_guard_262144':
                if mk(cfg,'SYNTH_MEMORY_MAX_BITS')!='131072':raise ValueError('unexpected memory guard')
                cfg=set_mk(cfg,'SYNTH_MEMORY_MAX_BITS','262144')
            elif strategy=='skip_incremental_repair':
                cfg=set_mk(cfg,'SKIP_INCREMENTAL_REPAIR','1')
            elif strategy=='floorplan_remove_abc_buffers':
                cfg=set_mk(cfg,'REMOVE_ABC_BUFFERS','1')
            elif strategy=='retain_ram_bank_hierarchy':
                evidence=json.loads((HERE/'c00766_hierarchy_evidence.json').read_text())
                files=mk(cfg,'VERILOG_FILES').split()
                if len(files)!=1 or sha(files[0])!=evidence['original_source_sha256']:
                    raise ValueError('RAM hierarchy candidate source differs from the diagnosed design')
                if mk(cfg,'SYNTH_MOCK_LARGE_MEMORIES') not in ('','0') or mk(cfg,'SYNTH_GUT') not in ('','0'):
                    raise ValueError('mocked or gutted memories are not an acceptable repair')
                if mk(cfg,'SYNTH_KEEP_MODULES'):raise ValueError('unexpected existing synthesis hierarchy policy')
                cfg=set_mk(cfg,'SYNTH_KEEP_MODULES','bram_4k_8')
                save(work/'hierarchy_evidence.json',evidence)
            if strategy=='area_expansion_1.25_port_buffer':
                if mk(cfg,'POST_GLOBAL_PLACE_TCL'):raise ValueError('existing placement hook needs explicit composition review')
                shutil.copy2(HERE/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl',work/'feedthrough.tcl')
                shutil.copy2(HERE/'c00435_compound_evidence.json',work/'area_feedthrough_evidence.json')
                cfg=set_mk(cfg,'POST_GLOBAL_PLACE_TCL','/work/feedthrough.tcl')
            if strategy.startswith(('area_expansion_','sv_frontend_area_expansion_','source_normalization_area_expansion_')):
                factor=float(strategy.removesuffix('_port_buffer').rsplit('_',1)[1]);die=list(map(float,mk(cfg,'DIE_AREA').split()));core=list(map(float,mk(cfg,'CORE_AREA').split()))
                if len(die)==4 and len(core)==4:
                    newdie=[die[0],die[1],math.ceil(die[2]*factor),math.ceil(die[3]*factor)]
                    newcore=[core[0],core[1],newdie[2]-(die[2]-core[2]),newdie[3]-(die[3]-core[3])]
                    cfg=set_mk(set_mk(cfg,'DIE_AREA',' '.join(map(str,newdie))),'CORE_AREA',' '.join(map(str,newcore)))
                elif mk(cfg,'CORE_UTILIZATION'):
                    cfg=set_mk(cfg,'CORE_UTILIZATION',str(round(float(mk(cfg,'CORE_UTILIZATION'))/factor**2,3)))
                else:raise ValueError('missing baseline area constraint; explicit sizing diagnosis required')
            if strategy=='source_normalization_pin_pdn_clearance':
                if mk(cfg,'PRE_IO_PLACEMENT_TCL'):raise ValueError('existing IO hook needs explicit composition review')
                shutil.copy2(HERE/'pin_pdn_clearance.tcl',work/'pin_pdn_clearance.tcl')
                shutil.copy2(HERE/'c03391_pin_geometry_evidence.json',work/'pin_geometry_evidence.json')
                cfg=set_mk(cfg,'PRE_IO_PLACEMENT_TCL','/work/pin_pdn_clearance.tcl')
            if strategy=='source_normalization_core_floor_32':
                if mk(cfg,'DIE_AREA') or mk(cfg,'CORE_AREA') or mk(cfg,'CORE_UTILIZATION')!='50':
                    raise ValueError('unexpected PHY baseline sizing; core floor requires a new review')
                # Same 32 um core floor and 2 um margin used by the frozen
                # baseline driver's declared PDN-0185 fallback; no PDN change.
                cfg=re.sub(r'^export\s+CORE_UTILIZATION\s*=.*\n?', '', cfg, flags=re.M)
                cfg=set_mk(set_mk(cfg,'DIE_AREA','0 0 36 36'),'CORE_AREA','2 2 34 34')
            assert (work/'constraint.sdc').read_bytes()==(baseline/'constraint.sdc').read_bytes()
            (work/'config.mk').write_text(cfg)
            delta={k:{'before':mk(basecfg,k),'after':mk(cfg,k)} for k in set(re.findall(r'^export\s+(\w+)',cfg,re.M)) if mk(basecfg,k)!=mk(cfg,k)}
            detail=dict(strategy=strategy,config_delta=delta,frontend_fixes=fixes,work=str(work),status='flow_running',
                        source_audit=str(work/'source_audit.json') if (work/'source_audit.json').exists() else None,
                        implementation_sha256={p.name:sha(p) for p in work.iterdir() if p.is_file() and
                            (p.suffix in ('.tcl','.py','.ys') or p.name in ('config.mk','constraint.sdc','slang.v','frontend.v','upstream_assets.json','source_audit.json','translation_evidence.json','source_order_evidence.json','conditional_include_evidence.json','pin_geometry_evidence.json','area_feedthrough_evidence.json','hierarchy_evidence.json','driver_replication_evidence.json','i2c_hierarchy_evidence.json','c03259_hierarchical_mapped.v','normal_recipe_evidence.json'))},
                        semantic_review=('bounded_four_state_original_i2c_traffic_and_two_driver_negative_controls_not_formal' if strategy=='preserve_i2c_tristate_hierarchy' else
                                         'bounded_four_state_original_rom_simulation_not_formal' if strategy=='internal_tristate_logic' else
                                         'exhaustive_four_state_original_inout_drive_cone_not_full_chip_equivalence' if strategy=='replicate_original_inout_drivers' else
                                         'bounded_two_state_traffic_simulation_not_formal' if name.startswith('C03980__') and is_sv else
                                         'independent_frontend_equivalence_default_configuration' if name.split('__')[0] in ('C03962','C03986') and is_sv else
                                         'independent_frontend_output_equivalence_default_configuration' if name.startswith('C03966__') and is_sv else
                                         'bounded_differential_simulation_passed_not_formal' if placement_retry else
                                         'compiler_transform_review_required' if is_sv or strategy=='aldff_to_dffsr' else 'no_functional_RTL_edit'))
            record['attempts'].append(detail);save(target/'progress.json',record)
            if native_project:
                detail['normal_recipe_project']=str(native_project)
                detail['semantic_review']='original_RTL_assets_and_SDC_unchanged_reviewed_input_list_and_normal_physical_recipe'
                save(target/'progress.json',record)
                record_native(native_project,status='flow_running',campaign_receipt=str(target/'progress.json'))
            started=time.time();rc=podman(work,tag,'cd "$ORFS_ROOT/flow" && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work',work/'flow.log')
            run=attempt/'runs'/run_name;driver.harvest(work,run)
            result=driver.Result(design=name,platform='sky130hd',period='5')
            if strategy=='source_normalization_core_floor_32':
                result.core_floor_um=32.0;result.pdn_fallback=True
            result.seconds=round(time.time()-started,1);result.stage=driver.last_stage(work)
            result.status='pass' if rc==0 and (run/'6_final.gds').is_file() else 'fail'
            log=(work/'flow.log').read_text(errors='replace');result.failure_class='' if result.status=='pass' else ('timeout' if rc==124 else driver.classify(log,result.stage))
            driver.read_metrics(work,result);save(run/'result.json',dataclasses.asdict(result))
            detail.update(status=result.status,seconds=result.seconds,run=str(run),failure_class=result.failure_class)
            if native_project:record_native(native_project,flow_returncode=rc,run=str(run),flow_seconds=result.seconds)
            if result.status=='pass':
                if strategy=='preserve_i2c_tristate_hierarchy':
                    final_audit=validate_i2c_final_netlist(run/'6_final.v',work/'flow.log')
                    save(run/'i2c_final_audit.json',final_audit);save(work/'i2c_final_audit.json',final_audit)
                    detail['final_i2c_topology_audit']=final_audit
                if strategy=='replicate_original_inout_drivers':
                    final_audit=validate_zube_final_netlist(run/'6_final.v')
                    save(run/'inout_driver_final_audit.json',final_audit)
                    save(work/'driver_replication_final_audit.json',final_audit)
                    detail['final_inout_cone_audit']=final_audit
                detail['status']='signoff_running';save(target/'progress.json',record)
                if native_project:record_native(native_project,status='signoff_running')
                drc.one_and_record(run,out/'drc_projects',TIMEOUT,'sky130hd')
                lvs.one_and_record(run,out/'lvs_projects',TIMEOUT,'sky130hd')
                signed=json.loads((run/'result.json').read_text());detail.update(drc=signed.get('drc_status'),lvs=signed.get('lvs_status'),status='completed')
                audited=inspect_run(run);save(run/'strict_signoff_audit.json',audited)
                detail.update(reported_drc=detail['drc'],reported_lvs=detail['lvs'],
                              drc=audited['drc'],lvs=audited['lvs'],acceptance_audit=audited)
                if native_project:record_native(native_project,strict_signoff=audited)
                if audited['dual_clean']:
                    record['status']='dual_clean';break
            record['status']='not_clean'
    except Exception as exc:
        record['status']='blocked' if not record['attempts'] else 'error'
        record['reason']=str(exc)
    finally:
        record['baseline_unchanged']=all(sha(baseline/k)==v for k,v in record['baseline_hashes'].items())
        if not record['baseline_unchanged']:raise RuntimeError('baseline mutated externally: '+name)
        if native_project:
            try:validate_execution(normal_trials,previous,PACKAGE,Path(record['attempts'][-1]['work']))
            except Exception as exc:record.update(status='error',reason='native replay integrity check: '+str(exc))
            record_native(native_project,status='error' if record['status']=='error' else 'completed',
                          campaign_status=record['status'],reason=record.get('reason'),
                          finished_at=time.strftime('%FT%T%z'))
        if native_lock:native_lock.close()
        save(receipt,record);save(target/'progress.json',record)
    print(time.strftime('%FT%T%z'),name,record['status'],record.get('reason',''),flush=True)
    return record


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=2);parser.add_argument('--canary',action='store_true');parser.add_argument('--include-expensive',action='store_true');parser.add_argument('--seed',type=Path)
    parser.add_argument('--exclude-design',action='append',default=[],help='Exact design to leave with an existing worker')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    if not 1<=args.workers<=2:parser.error('this campaign reserves at most 16 cores (2 workers)')
    inv=json.loads(args.inventory.read_text());assert inv['total']==1249
    if not inv.get('acceptance_audit'):raise ValueError('requires top-level audited inventory; historical clean strings are insufficient')
    baseline_clean=inv['dual_clean']
    driver=module('sky130_baseline_driver',D/'orfs_spec.py');drc=module('sky130_baseline_drc',D/'signoff_pass.py');lvs=module('sky130_baseline_lvs',D/'lvs130.py')
    overlay=HERE/'checker_strict_verdict'
    normalizer=overlay/'scripts/flow/normalize_diode_spice.py'
    if sha(normalizer)!='9794688cacd6a9443234901de120b1b150c69364312fa5c37f3dc0660ffe73a2':
        raise ValueError('unreviewed SPICE continuation normalizer')
    for name,digest in {
        'run_netgen_lvs.sh':'93cfc7520c59c3f36ca09f0713ce3efa2e285a492e2cf87d3a85aee01561f94f',
        'normalize_lvs_identifiers.py':'d3233aa0364e0acacc081e549cc970ebdd30438927a8bd37b85e8da7000d84fd',
        'netgen_final_verdict.py':'bd92de492dce9cb5064654822075f3e34d9bdafbc74b46586eae04224ac8fe4f',
    }.items():
        if sha(overlay/'scripts/flow'/name)!=digest:raise ValueError('unreviewed identifier encoding helper')
    lvs.R2G=overlay  # Same tools/deck; preserve lines and use invertible identifier encoding.
    cases=inv['failures']
    if args.canary:cases=[c for c in cases if c['design'].split('__')[0] in ('C00303','C02098','C00241','C02620')]
    elif not args.include_expensive:cases=[c for c in cases if not (c['category'].startswith('timeout_') or c['category']=='memory_exhaustion')]
    cases=[c for c in cases if c['design'] not in args.exclude_design]
    scope=args.out.parent/'repair_scope.json'
    if scope.exists():
        from repair_scope import load as load_scope, select as select_scope
        cases=select_scope(cases,load_scope(scope),json.loads(scope.read_text()).get('externally_managed_designs',[]))
    cases=order_cases(cases,args.canary)
    manifest=dict(baseline=inv['baseline'],baseline_dual_clean=baseline_clean,total=1249,selected=len(cases),workers=args.workers,cores_per_flow=CORES,
        policy_revision=POLICY_REVISION,runner_sha256=sha(Path(__file__)),
        repair_scope_sha256=sha(scope) if scope.exists() else None,
        queue_order=[dict(design=c['design'],scheduling_seconds=queue_estimate(c)[0],basis=queue_estimate(c)[1]) for c in cases],
        frontend_sha256=sha(Path(frontend.__file__)),
        lvs_checker_overlay=dict(path=str(overlay),normalizer_sha256=sha(normalizer),
            provenance=json.loads((overlay/'overlay_provenance.json').read_text())),
        source_helpers_sha256={n:sha(HERE/n) for n in ('source_audit.py','upstream_assets.py')},
        flow_timeout=TIMEOUT,signoff_timeout=TIMEOUT,memory_limit_per_flow_gb=64,clock_constraints='byte-identical to baseline',
        checkers={str(p):sha(p) for p in [D/'signoff_pass.py',D/'lvs130.py',D/'r2g/signoff-loop/scripts/flow/run_drc.sh',D/'r2g/signoff-loop/scripts/flow/run_netgen_lvs.sh']},
        source_policy='private RTL copies; existing data only, never invent missing initialization',started_at=time.strftime('%FT%T%z'))
    save(args.out/'manifest.json',manifest)
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures=[executor.submit(run_case,c,args.out,driver,drc,lvs,args.seed) for c in cases]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result());wins=sum(r['status']=='dual_clean' for r in results)
            save(args.out/'summary.json',dict(completed=len(results),selected=len(cases),new_dual_clean=wins,combined_dual_clean=baseline_clean+wins,
                results=[{'design':r['design'],'status':r['status']} for r in results]))
    print('DONE',len(results),'new_dual_clean',sum(r['status']=='dual_clean' for r in results),flush=True)


if __name__=='__main__':main()
