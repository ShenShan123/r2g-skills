"""Witness unidirectional top-port aliases without modifying a layout or RTL."""
from collections import defaultdict
import hashlib
from pathlib import Path
import re


def diagnose(def_path, netlist_path):
    raw=Path(def_path).read_bytes();mapped=Path(netlist_path).read_bytes()
    verilog=mapped.decode(errors='replace')
    verilog=re.sub(r'/\*.*?\*/|//[^\n]*','',verilog,flags=re.S)
    # The existing repair hook treats every non-input BTerm as an output.
    # Scope transfer must therefore refuse real bidirectional declarations.
    if re.search(r'\binout\b',verilog):return None
    text=raw.decode(errors='replace')
    section=re.search(r'\bPINS\s+(\d+)\s*;(.*?)END PINS',text,re.S)
    if not section:return None
    groups=defaultdict(list);names=set();parsed=0;supplies=0
    for match in re.finditer(r'-\s+(\S+)\s+\+ NET\s+(\S+)(.*?);',section[2],re.S):
        pin,net,body=match.groups()
        directions=re.findall(r'\+ DIRECTION\s+(\w+)',body)
        if len(directions)!=1 or pin in names:return None
        names.add(pin);parsed+=1
        if (re.search(r'\+ SPECIAL\b',body) and re.search(r'\+ USE (?:POWER|GROUND)\b',body)):
            supplies+=1
            continue
        if directions[0] not in ('INPUT','OUTPUT'):return None
        groups[net].append(dict(pin=pin,direction=directions[0]))
    if parsed!=int(section[1]) or not parsed:return None
    aliases=[]
    for net,ports in groups.items():
        if len(ports)<2:continue
        inputs=[p for p in ports if p['direction']=='INPUT']
        outputs=[p for p in ports if p['direction']=='OUTPUT']
        if len(inputs)!=1 or not outputs:return None
        aliases.append(dict(net=net,input=inputs[0]['pin'],outputs=[p['pin'] for p in outputs]))
    if not aliases:return None
    return dict(mechanism='unidirectional_top_port_feedthrough',
                parsed_port_count=parsed,ignored_special_supply_ports=supplies,shared_top_port_nets=len(aliases),
                aliased_output_count=sum(len(a['outputs']) for a in aliases),
                inout_declaration_count=0,all_aliases_have_single_input=True,
                def_path=str(def_path),def_sha256=hashlib.sha256(raw).hexdigest(),
                mapped_netlist_path=str(netlist_path),mapped_netlist_sha256=hashlib.sha256(mapped).hexdigest(),
                examples=aliases[:5])
