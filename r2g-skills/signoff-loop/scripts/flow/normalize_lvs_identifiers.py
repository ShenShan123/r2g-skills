#!/usr/bin/env python3
"""Encode escaped negative indices and proven case-distinct ports for Netgen.

OpenROAD writes a scalar port such as \\cfgfb[-1] in Verilog. Netgen 1.5.272
keeps that escape in its pin name while reading the corresponding SPICE name
without it. Apply a collision-free alpha-renaming to BOTH generated comparison
inputs, never to RTL, DEF or GDS. Preserve all ports/devices/connectivity and
prove exact inverses. Optional Magic evidence permits case-alias encoding only
for distinct extracted port nodes. Unsupported transformations fail closed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ESCAPED = re.compile(r'\\([^\s]+)(?=\s)')
NEGATIVE = re.compile(r'[A-Za-z_][\w$]*\[-\d+\]')


def encode_case_ports(body, verilog, top, header, ext_data):
    """Rename case aliases only with independent Magic-node evidence.

    Restrict reference edits to generated module ports, scalar declarations,
    and scalar named-pin connections. Library formal pins, cell/instance names,
    comments and escaped identifiers are never signal replacement sites.
    """
    if ext_data is None:return body,verilog,{}
    declared,uf=ext_data
    ports=set(re.findall(r'^\s*(?:input|output|inout)\s+([A-Za-z_][\w$]*)\s*;',verilog,re.M))
    groups={}
    for name in ports & header:groups.setdefault(name.lower(),set()).add(name)
    candidates=set()
    for names in groups.values():
        if len(names)<2:continue
        if not names<=declared:raise ValueError('case aliases lack Magic port evidence')
        # Every implicated port must be the sole declared port in its merge
        # class. In particular, a real short must never be encoded away.
        for name in names:
            if {p for p in declared if uf.find(p)==uf.find(name)}!={name}:
                raise ValueError('case aliases include a real merged port')
        candidates.update(names)
    if not candidates:return body,verilog,{}
    mapping={old:'r2g_lvs_ident_'+hashlib.sha256(old.encode()).hexdigest()[:20] for old in sorted(candidates)}
    if any(new.lower() in (body+verilog).lower() for new in mapping.values()):raise ValueError('identifier collision')

    # Tokenize once, retaining whitespace exactly. A candidate is legal only
    # as a top header pin or an X-instance node (never its name or cell model).
    edits=[];logical=[]
    def visit(tokens):
        if not tokens:return
        first=tokens[0][0]
        if first.lower()=='.subckt':allowed=range(2,len(tokens))
        elif first[0].lower()=='x':allowed=range(1,len(tokens)-1)
        else:allowed=()
        for i,(token,start,end) in enumerate(tokens):
            if token in mapping:
                if i not in allowed:raise ValueError('case alias outside SPICE net position')
                edits.append((start,end,mapping[token]))
    offset=0
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith('*'):
            offset+=len(line);continue
        continuation=line.startswith('+')
        if not continuation:visit(logical);logical=[]
        logical.extend((m[0],offset+m.start(),offset+m.end()) for m in re.finditer(r'\S+',line) if not (continuation and m.start()==0))
        offset+=len(line)
    visit(logical)
    updated=body
    for start,end,value in sorted(edits,reverse=True):updated=updated[:start]+value+updated[end:]

    # Hide comments/strings/escaped names without changing positions. This is
    # intentionally a conservative flat-netlist adapter, not a Verilog parser.
    masked=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\\[^\s]+',
                  lambda m:' '*len(m[0]),verilog)
    spans=[]
    module=re.search(r'\bmodule\s+'+re.escape(top)+r'\s*\(([^;]*?)\)\s*;',masked)
    if not module:raise ValueError('unsupported generated module header')
    spans.append(module.span(1))
    spans.extend(m.span(1) for m in re.finditer(r'\b(?:input|output|inout|wire)\s+([A-Za-z_][\w$]*)\s*;',masked))
    spans.extend(m.span(1) for m in re.finditer(r'\.\s*[A-Za-z_][\w$]*\s*\(\s*([A-Za-z_][\w$]*)\s*\)',masked))
    v_edits=[]
    for m in re.finditer(r'[A-Za-z_][\w$]*',masked):
        if m[0] not in mapping:continue
        if any(start<=m.start() and m.end()<=end for start,end in spans):
            v_edits.append((m.start(),m.end(),mapping[m[0]]))
        elif masked[:m.start()].rstrip().endswith('.'):
            continue  # Library formal pin, not a signal.
        else:raise ValueError('case alias outside supported Verilog net position')
    reference=verilog
    for start,end,value in reversed(v_edits):reference=reference[:start]+value+reference[end:]
    inverse_body=updated;inverse_v=reference
    for old,new in mapping.items():inverse_body=inverse_body.replace(new,old);inverse_v=inverse_v.replace(new,old)
    if inverse_body!=body or inverse_v!=verilog:raise ValueError('non-bijective case encoding')
    return updated,reference,mapping


def normalize(spice, verilog, top, ext_data=None):
    # This helper operates on OpenROAD's flat, generated reference only.
    modules=re.findall(r'^\s*module\s+(\S+)',verilog,re.M)
    if modules!=[top]:return spice,verilog,{}
    lines=spice.splitlines(keepends=True);start=end=None;header_end=None
    for i,line in enumerate(lines):
        tokens=line.split()
        if len(tokens)>1 and tokens[0].lower()=='.subckt' and tokens[1]==top:
            if start is not None:raise ValueError('duplicate top subcircuit')
            start=i;header_end=i+1
            while header_end<len(lines) and lines[header_end].startswith('+'):header_end+=1
        if start is not None and end is None and line.lower().startswith('.ends'):end=i
    if start is None or end is None:return spice,verilog,{}
    header=set(' '.join(lines[start:header_end]).split())
    candidates=sorted({m.group(1) for m in ESCAPED.finditer(verilog) if NEGATIVE.fullmatch(m.group(1))})
    mapping={}
    original_body=''.join(lines[start:end]);body=original_body;reference=verilog
    for old in candidates:
        # A real missing/different layout port must not be invented or matched.
        if old not in header:continue
        declaration=re.compile(r'\b(?:input|output|inout)\s+\\'+re.escape(old)+r'(?=\s)')
        if not declaration.search(verilog):continue
        new='r2g_lvs_ident_'+hashlib.sha256(old.encode()).hexdigest()[:20]
        if new.lower() in (spice+verilog+body+reference).lower():raise ValueError('identifier collision')
        # Refuse case-fold aliases: Netgen's SPICE reader is case insensitive.
        if any(t!=old and t.lower()==old.lower() for t in original_body.split()):
            raise ValueError('ambiguous case-folded identifier')
        body,n=re.subn(r'(?<!\S)'+re.escape(old)+r'(?!\S)',new,body)
        reference,m=ESCAPED.subn(lambda x:new if x.group(1)==old else x.group(0),reference)
        if n==0:raise ValueError('missing layout token')
        mapping[old]=new
    restored_body=body;restored_reference=reference
    for old,new in mapping.items():
        restored_body=restored_body.replace(new,old)
        restored_reference=restored_reference.replace(new,'\\'+old)
    if restored_body!=original_body or restored_reference!=verilog:
        raise ValueError('non-bijective identifier transformation')
    body,reference,case_mapping=encode_case_ports(body,reference,top,header,ext_data)
    mapping.update(case_mapping)
    return ''.join(lines[:start])+body+''.join(lines[end:]),reference,mapping


def main():
    p=argparse.ArgumentParser();p.add_argument('spice',type=Path);p.add_argument('verilog',type=Path);p.add_argument('top');p.add_argument('--ext',type=Path);a=p.parse_args()
    original_s=a.spice.read_text();original_v=a.verilog.read_text()
    ext_data=None
    if a.ext:
        from restore_ext_ports import parse_ext
        ext_data=parse_ext(str(a.ext))
    s,v,mapping=normalize(original_s,original_v,a.top,ext_data)
    if not mapping:return
    before={}
    for path,original,updated in ((a.spice,original_s,s),(a.verilog,original_v,v)):
        backup=path.with_name(path.name+'.before_identifier_encoding')
        with backup.open('x') as f:f.write(original)
        before[str(path)]=hashlib.sha256(original.encode()).hexdigest()
        temp=path.with_name(path.name+'.identifier_encoding.tmp');temp.write_text(updated);temp.replace(path)
    manifest=dict(top=a.top,mapping=mapping,exact_inverse_verified=True,before_sha256=before,
                  scope='comparison-input alpha-renaming only; original interfaces and connectivity preserved')
    (a.spice.parent/'identifier_encoding.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('[normalize_lvs_identifiers] encoded '+str(len(mapping))+' comparison port name(s)')


if __name__=='__main__':main()
