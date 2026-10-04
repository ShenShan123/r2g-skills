"""Independent endpoint/BFS wirelength oracle from Yosys JSON, DEF and Liberty."""
import argparse
from collections import defaultdict, deque
import csv
import functools
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gnn-node'))
from congestion_audit import section
from stage_data import digest, labels, load_graph, save_json
from experiment4_semantic_audit import logical_oracle, read_routed_lengths

LIB_PARSER = Path(__file__).resolve().parents[1]/'r2g-skills/def-graph/scripts/extract/techlib/liberty.py'
spec = importlib.util.spec_from_file_location('oracle_liberty', LIB_PARSER)
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)


def canonical(value):
    return value.replace('\\', '').replace('/', '.').strip()


@functools.lru_cache(maxsize=8)
def transparent_cells(paths):
    result = {}
    for name, cell in lib.load_liberty_db(list(paths))['cells'].items():
        if cell.get('is_sequential'):
            continue
        pins = cell['pins']
        ins = [n for n,p in pins.items() if p.get('direction','').upper()=='INPUT']
        outs = [n for n,p in pins.items() if p.get('direction','').upper()=='OUTPUT']
        if len(ins)!=1 or len(outs)!=1:
            continue
        function = re.sub(r'[\s()]', '', pins[outs[0]].get('function',''))
        if function in (ins[0], '!'+ins[0], '~'+ins[0], ins[0]+"'"):
            result[name] = (ins[0],outs[0])
    if not result:
        raise ValueError('No Liberty-confirmed buffer/inverter masters')
    return result


def def_connectivity(text):
    masters = {canonical(n): b.split()[0].upper() for n,b in section(text,'COMPONENTS')}
    connections = {}
    endpoint_net = {}
    for raw,body in section(text,'NETS'):
        name=canonical(raw)
        if name in connections:
            raise ValueError('Canonical physical net collision')
        pins=[(canonical(i),canonical(p)) for i,p in re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)',body.split('+',1)[0])]
        connections[name]=pins
        for pin in pins:
            if pin in endpoint_net and endpoint_net[pin]!=name:
                raise ValueError('Physical endpoint on multiple nets')
            endpoint_net[pin]=name
    for raw,body in section(text,'PINS'):
        net=re.search(r'\+\s*NET\s+(\S+)',body)
        if net:
            pin=('PIN',canonical(raw));name=canonical(net[1])
            if pin in endpoint_net and endpoint_net[pin]!=name:
                raise ValueError('DEF PIN/NETS disagreement')
            endpoint_net[pin]=name
    return masters, connections, endpoint_net


def reconstruct(module, text, transparent, lengths):
    truth=logical_oracle(module)
    aliases={canonical(n):b for n,b in truth['aliases'].items()}
    base_cells={canonical(n) for n in module['cells']}
    masters,nets,physical=def_connectivity(text)
    endpoints=defaultdict(set)
    for pin,bit in truth['edges'][('pin','connects_to','net')]:
        endpoints[bit].add(tuple(canonical(x) for x in pin))
    for pin,bit in truth['edges'][('io_pin','connects_to','net')]:
        endpoints[bit].add(('PIN',canonical(pin)))
    adjacency={n:set() for n in nets}
    bridges=[]
    for inst,master in masters.items():
        if inst in base_cells or master not in transparent:
            continue
        left,right=(physical.get((inst,p)) for p in transparent[master])
        if left in nets and right in nets:
            adjacency[left].add(right);adjacency[right].add(left)
            bridges.append((inst,master,left,right))
    components={};members=[]
    for start in nets:
        if start in components:
            continue
        group=len(members);queue=deque([start]);nodes=set([start]);components[start]=group
        while queue:
            for other in adjacency[queue.popleft()]:
                if other not in components:
                    components[other]=group;nodes.add(other);queue.append(other)
        members.append(nodes)
    anchored=defaultdict(set);owners=defaultdict(set)
    for bit,pins in endpoints.items():
        for pin in pins:
            net=physical.get(pin)
            if net in components:
                anchored[bit].add(net);owners[components[net]].add(bit)
    values={}
    for bit in truth['net']:
        roots={components[n] for n in anchored[bit]}
        conflict=any(owners[g]!={bit} for g in roots)
        assigned=set().union(*(members[g] for g in roots)) if roots else set()
        valid=bool(assigned) and not conflict and all(n in lengths for n in assigned)
        values[bit]=dict(valid=valid, nets=sorted(assigned), direct=sorted(anchored[bit]),
            value=sum(lengths[n] for n in assigned) if valid else None,
            conflict=conflict, mapped_pins=sum(p in physical for p in endpoints[bit]),
            total_pins=len(endpoints[bit]))
    return aliases,values,bridges


def audit(record, output, yosys):
    root=Path(record['graphs']['route']['path']).parents[2]
    cfg_path=root.parent/'method_config.json';cfg=json.loads(cfg_path.read_text())
    raw=Path(cfg.get('label_def') or cfg['route_def'])
    sidecar=root/'labels/net_wirelength_Cg.csv'
    inputs=[cfg_path,raw,sidecar,Path(cfg['yosys_v']),*map(Path,cfg['lib'])]
    identity={str(p):digest(p) for p in inputs+[Path(__file__),LIB_PARSER,Path(yosys),
                                              Path(__file__).with_name('experiment4_semantic_audit.py')]}
    work=output/record['design_id'];work.mkdir(parents=True,exist_ok=True)
    report_path=work/'report.json'
    if report_path.exists():
        old=json.loads(report_path.read_text())
        if old['inputs']!=identity or old['graphs']!=record['graphs']:
            raise ValueError('Audit identity changed; use a new output directory')
        return old
    ys=work/'read.ys'
    ys.write_text(f'read_verilog {json.dumps(cfg["yosys_v"])}\nwrite_json {json.dumps(str(work/"yosys.json"))}\n')
    with (work/'yosys.log').open('w') as log:
        subprocess.run([str(yosys),'-Q','-T','-s',str(ys)],stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
    module=json.loads((work/'yosys.json').read_text())['modules'][cfg['top_module']]
    lengths={canonical(n):v for n,v in read_routed_lengths(raw).items()}
    aliases,values,bridges=reconstruct(module,raw.read_text(),transparent_cells(tuple(cfg['lib'])),lengths)
    with sidecar.open(newline='') as f: rows=list(csv.DictReader(f))
    errors=[];verified=[];missing=[];maximum=0.
    for row in rows:
        name=row['net_name'];bit=aliases.get(canonical(name));expected=values.get(bit)
        if expected is None:
            missing.append(name);continue
        valid=bool(int(row['wirelength_valid']))
        if valid!=expected['valid']:
            errors.append(dict(net=name,kind='mask',observed=valid,oracle=expected))
        elif valid:
            actual=float(row['wirelength_um']);value=expected['value'];maximum=max(maximum,abs(actual-value))
            if not math.isclose(actual,value,rel_tol=1e-5,abs_tol=.0011):
                errors.append(dict(net=name,kind='value',observed=actual,oracle=expected))
            if int(row['route_segment_net_count'])!=len(expected['nets']):
                errors.append(dict(net=name,kind='segment_count',oracle=expected))
            verified.append(dict(net=name,physical_nets=expected['nets'],expected_um=value))
    by_name={r['net_name']:r for r in rows}
    if len(by_name)!=len(rows):
        raise ValueError('Duplicate label names')
    for stage in record['graphs']:
        graph=load_graph(record,stage,'wirelength');y,mask=labels(graph,'wirelength')
        if set(graph['net'].net_name)!=set(by_name):
            raise ValueError('Tensor/CSV net identities differ')
        for i,name in enumerate(graph['net'].net_name):
            row=by_name[name]
            if bool(mask[i])!=bool(int(row['wirelength_valid'])):
                raise ValueError('Tensor/CSV mask disagreement')
            if mask[i] and not math.isclose(float(y[i]),float(row['wirelength_um']),rel_tol=1e-6,abs_tol=.0011):
                raise ValueError('Tensor/CSV value disagreement')
    report=dict(design_id=record['design_id'],status='FAIL' if errors or missing else 'PASS',
        canonical_nets=len(rows),valid_labels=len(verified),missing_aliases=missing,errors=errors,
        max_abs_error_um=maximum,transparent_bridges=bridges,verified_mapping=verified,
        inputs=identity,graphs=record['graphs'])
    save_json(report_path,report)
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--yosys',type=Path,required=True)
    p.add_argument('--limit',type=int,default=0)
    args=p.parse_args();manifest=json.loads(args.manifest.read_text());reports=[]
    args.output.mkdir(parents=True,exist_ok=True)
    for record in manifest['records'][:args.limit or None]:
        result=audit(record,args.output,args.yosys);reports.append(result)
        save_json(args.output/'status.json',dict(processed=len(reports),planned=len(manifest['records']),
            passed=sum(r['status']=='PASS' for r in reports),active=record['design_id']))
        print(json.dumps({k:result[k] for k in ('design_id','status','valid_labels','max_abs_error_um')}),flush=True)
        if result['status']!='PASS':
            print(json.dumps(dict(errors=result['errors'][:3],missing=result['missing_aliases'][:5])),flush=True)
    save_json(args.output/'summary.json',dict(status='PASS' if all(r['status']=='PASS' for r in reports) else 'FAIL',designs=len(reports),
        valid_labels=sum(r['valid_labels'] for r in reports),manifest_sha256=digest(args.manifest),
        reports={r['design_id']:digest(args.output/r['design_id']/'report.json') for r in reports},
        scope='All canonical net masks and supervised values via raw Yosys/DEF endpoint BFS and Liberty unary functions.'))


if __name__=='__main__':
    main()
