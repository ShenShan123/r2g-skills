"""Conservative fixed-area route candidate; physical A/B promotion is mandatory."""
import hashlib
import re
import os
import shutil
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

# V1 supports the simple generated standard-cell configs in the frozen cohort.
# Unknown floorplan/PDN/macro overrides fail closed rather than being guessed.
_ALLOWED = {'DESIGN_NAME','PLATFORM','VERILOG_FILES','VERILOG_INCLUDE_DIRS',
            'VERILOG_DEFINES','SDC_FILE','ABC_AREA','SYNTH_MEMORY_MAX_BITS',
            'DIE_AREA','CORE_AREA'}

def _layout(root):
    definition=root/'6_final.def'
    if definition.is_file():
        data=definition.read_bytes()
        return data.decode(errors='replace'), {'def':str(definition),'def_sha256':hashlib.sha256(data).hexdigest()}
    odb=root/'3_place.odb'
    executable=os.environ.get('R2G_LAYOUT_OPENROAD') or shutil.which('openroad')
    if not odb.is_file() or not executable:return None
    # Tcl braced paths are literal except backslash-newline; reject unusual paths
    # rather than interpolate an executable Tcl expression from a project path.
    if any(c in str(odb.resolve()) for c in '{}\\\n\r'):return None
    digest=hashlib.sha256(odb.read_bytes()).hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix='r2g_area_geometry_') as tmp:
            tmp=Path(tmp);script=tmp/'read.tcl';dest=tmp/'layout.def'
            script.write_text('set_thread_count 1\nread_db {'+str(odb.resolve())+'}\nwrite_def {'+str(dest)+'}\nexit\n')
            with (tmp/'read.log').open('w') as log:
                process=subprocess.run([executable,'-exit',str(script)],stdout=log,stderr=log,timeout=30)
            if process.returncode or not dest.is_file():return None
            data=dest.read_bytes()
        if hashlib.sha256(odb.read_bytes()).hexdigest()!=digest:return None
        return data.decode(errors='replace'), {'odb':str(odb),'odb_sha256':digest,
            'derived_def_sha256':hashlib.sha256(data).hexdigest(),
            'openroad_sha256':hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
            'read_only_odb_export':True}
    except (OSError,subprocess.TimeoutExpired):return None


def candidate(project, route, cfg):
    project=Path(project)
    if cfg.get('PLATFORM')!='sky130hd' or set(cfg)-_ALLOWED:return None
    if route.get('status') not in ('fail','timeout','residual'):return None
    if any((project/'constraints'/n).exists() for n in ('floorplan.json','floorplan.tcl','pin_order.cfg','macro_placement.cfg')):return None
    try:
        die=[Decimal(x) for x in cfg['DIE_AREA'].split()]
        core=[Decimal(x) for x in cfg['CORE_AREA'].split()]
        if len(die)!=4 or len(core)!=4 or not all(x.is_finite() for x in die+core):return None
        x,y,X,Y=die;a,b,A,B=core
        if not (x<a<A<X and y<b<B<Y):return None
    except (KeyError,InvalidOperation,ValueError):return None
    # Bind to a real retained route failure, not a caller-invented status alone.
    roots=[project]+sorted(project.glob('backend/RUN_*/results'),reverse=True)
    evidence=None
    for root in roots:
        log=root/'flow.log'
        if not log.is_file():log=root.parent/'flow.log'
        if not log.is_file():continue
        text=log.read_text(errors='replace')
        if not re.search(r'\[ERROR GRT-(?:0116|0118|0232)\]|stubborn tiles',text,re.I):continue
        layout=_layout(root)
        if layout is None:continue
        body,layout_evidence=layout
        units=re.search(r'UNITS DISTANCE MICRONS (\d+)\s*;',body)
        box=re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;',body)
        comps=re.search(r'COMPONENTS\s+(\d+)\s*;(.*?)END COMPONENTS',body,re.S)
        if not units or not box or not comps or int(units[1])<=0:continue
        if [Decimal(v)/Decimal(units[1]) for v in box.groups()]!=die:continue
        entries=re.findall(r'^\s*-\s+(\S+)\s+(\S+)\s(.*?);',comps[2],re.M|re.S)
        cells=[entry[1] for entry in entries]
        if not cells or len(cells)!=int(comps[1]):continue
        if any(not c.startswith('sky130_fd_sc_hd__') for c in cells):continue
        if any(re.search(r'\+\s*(FIXED|COVER)\b',attrs) and not (master=='sky130_fd_sc_hd__tapvpwrvgnd_1' and re.fullmatch(r'TAP_TAPCELL_ROW_\d+_\d+',name)) for name,master,attrs in entries):continue
        evidence={**layout_evidence,
                  'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'standard_cell_count':len(cells)}
        break
    if evidence is None:return None
    # Match historical bounded 1.25 linear relief, retaining all four margins.
    nx=x+((X-x)*Decimal('1.25')).to_integral_value(rounding=ROUND_CEILING)
    ny=y+((Y-y)*Decimal('1.25')).to_integral_value(rounding=ROUND_CEILING)
    newdie=[x,y,nx,ny];newcore=[a,b,nx-(X-A),ny-(Y-B)]
    fmt=lambda values:' '.join(format(v,'f') for v in values)
    return {'id':'fixed_area_route_relief','rationale':'Expand a witnessed standard-cell congested die by 1.25 per dimension while preserving core margins, RTL and timing/signoff constraints.',
            'config_edits':{'DIE_AREA':fmt(newdie),'CORE_AREA':fmt(newcore)},
            'rerun_from':'floorplan','recheck':'route','auto_apply':True,
            'requires_ab_promotion':True,'geometry_evidence':evidence,
            'fixed_area_scope_evidence':{'platform':'sky130hd','standard_cell_generated_config':True,
                'original_die':cfg['DIE_AREA'],'original_core':cfg['CORE_AREA']}}
