"""Bounded reuse of the physically validated unidirectional port-buffer repair."""
import hashlib
from pathlib import Path
import re
from physical_ab_scope import has_durable_repeated_wins

KEY=dict(symptom_id='a0d6b4c6ae5c8c4c',design_class='logic/small',
         platform='sky130hd',strategy='lvs_port_feedthrough_buffer')
HOOK=Path(__file__).resolve().parents[1]/'scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl'
HOOK_SHA256='98baa3fb699c67fa72a195184f16fae8ad760f383459c54f339b10dc31b18b29'


def matches(strategy):
    evidence=strategy.get('port_feedthrough_evidence') or {}
    if (strategy.get('id')!=KEY['strategy'] or strategy.get('sdc_edits') or strategy.get('env_edits')
            or strategy.get('config_edits')!={'POST_GLOBAL_PLACE_TCL':str(HOOK)}
            or strategy.get('rerun_from')!='place' or strategy.get('recheck')!='lvs'
            or evidence.get('mechanism')!='unidirectional_top_port_feedthrough'
            or evidence.get('all_aliases_have_single_input') is not True
            or evidence.get('inout_declaration_count')!=0):
        return False
    for name in ('shared_top_port_nets','aliased_output_count'):
        if type(evidence.get(name)) is not int or evidence[name]<1:return False
    if evidence['aliased_output_count']<evidence['shared_top_port_nets']:return False
    if not all(re.fullmatch('[0-9a-f]{64}',str(evidence.get(k,''))) for k in ('def_sha256','mapped_netlist_sha256')):return False
    try:
        return hashlib.sha256(HOOK.read_bytes()).hexdigest()==HOOK_SHA256
    except OSError:
        return False


def validated(conn,lifecycle,strategy):
    return matches(strategy) and has_durable_repeated_wins(conn,lifecycle,KEY)
