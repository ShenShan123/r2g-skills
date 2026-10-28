"""Reuse the validated default-SKY130 pin/PDN repair only with current witnesses."""
import hashlib
import math
from pathlib import Path
import re
from physical_ab_scope import has_durable_repeated_wins

KEY=dict(symptom_id='04d38c5a585fd332',design_class='logic/small',platform='sky130hd',strategy='pin_pdn_clearance')
HOOK=Path(__file__).resolve().parents[1]/'scripts/flow/orfs_hooks/pin_pdn_clearance.tcl'
HOOK_SHA256='9338d25a0f60802dcd71ea0a40b011a96e45c2f03c63d91f1a2b648e3fae94b6'


def matches(strategy):
    e=strategy.get('geometry_evidence') or {}
    if (strategy.get('id')!=KEY['strategy'] or strategy.get('sdc_edits') or strategy.get('env_edits')
            or strategy.get('config_edits')!={'PRE_IO_PLACEMENT_TCL':str(HOOK)}
            or strategy.get('rerun_from')!='floorplan' or strategy.get('recheck')!='drc'
            or (strategy.get('pdn_scope_evidence') or {}).get('generated_default_pdn_config') is not True
            or e.get('rule')!='m3.2' or e.get('units_per_um')!=1000 or e.get('required_spacing_um')!=0.3):return False
    count=e.get('marker_count');covered=e.get('covered_marker_count')
    if type(count) is not int or count<2 or type(covered) is not int or not 0.8*count<=covered<=count:return False
    if not all(re.fullmatch('[0-9a-f]{64}',str(e.get(k,''))) for k in ('def_sha256','report_sha256')):return False
    seen=set()
    for witness in e.get('witnesses',[]):
        gap=witness.get('gap_upper_bound_um')
        if (witness.get('layer')!='met3' or witness.get('power_net') not in ('VDD','VSS')
                or isinstance(gap,bool) or not isinstance(gap,(int,float)) or not math.isfinite(gap)
                or not 0<=gap<0.3):return False
        indices=witness.get('marker_indices',[])
        if not indices or any(type(i) is not int or not 0<=i<count for i in indices):return False
        seen.update(indices)
    if len(seen)!=covered:return False
    try:return hashlib.sha256(HOOK.read_bytes()).hexdigest()==HOOK_SHA256
    except OSError:return False


def validated(conn,lifecycle,strategy):
    return matches(strategy) and has_durable_repeated_wins(conn,lifecycle,KEY)
