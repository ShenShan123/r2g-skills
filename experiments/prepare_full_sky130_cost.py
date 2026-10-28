#!/usr/bin/env python3
"""Inventory all historical SKY130 rescues using original baseline metadata.

Reads remote source hashes, paths and sizes only; does not copy RTL/ROM contents
or execute EDA/model calls. Keeps every historically verified case, including
compiler transforms whose semantic review remains pending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


REMOTE_READER = r'''
import hashlib, json, re
from pathlib import Path

def run(designs, baseline):
    out=[]
    for design in designs:
        record={'design':design, 'errors':[], 'files':{}, 'asset_references':[]}
        matches=list(Path(baseline).glob(design+'__sky130hd__*'))
        if len(matches)!=1:
            record['errors'].append('baseline_run_count='+str(len(matches)));out.append(record);continue
        run_dir=matches[0];record['baseline_remote_run']=str(run_dir)
        config=(run_dir/'config.mk').read_text()
        def variable(name):
            m=re.search(r'^export\s+'+name+r'\s*[:?+]?=\s*(.*)$',config,re.M)
            return m.group(1).split() if m else []
        sources=[Path(s) for s in variable('VERILOG_FILES')]
        includes=[Path(s) for s in variable('VERILOG_INCLUDE_DIRS')]
        certified_dir=Path('/proj/workarea/yangao/rtl-certified-handoff-0002/certified')/design
        certified=json.loads((certified_dir/'DESIGN.json').read_text())
        certified_sources={(certified_dir/r['file']).resolve():r['sha256'] for r in certified['source_files']}
        record['certified_source_manifest']=str(certified_dir/'DESIGN.json')
        record['certified_source_manifest_sha256']=hashlib.sha256((certified_dir/'DESIGN.json').read_bytes()).hexdigest()
        record['defines']=certified.get('defines',[])
        if any(str(p).startswith('/work/') for p in sources):
            sources=list(certified_sources)
            includes=[(certified_dir/p).resolve() for p in certified.get('include_dirs',[])]
            record['source_path_resolution']='certified original manifest for container-staged filenames'
        else:
            record['source_path_resolution']='baseline config absolute originals'
        record.update(top=variable('DESIGN_NAME')[0],
            baseline_config_sha256=hashlib.sha256(config.encode()).hexdigest(),
            baseline_sdc_sha256=hashlib.sha256((run_dir/'constraint.sdc').read_bytes()).hexdigest())
        roots={Path(*p.parts[:p.parts.index('sources')+2]) for p in sources if 'sources' in p.parts}
        if len(roots)!=1:
            record['errors'].append('original_source_root_count='+str(len(roots)));out.append(record);continue
        root=roots.pop();record['original_root']=str(root)
        record['source_files']=[str(p.relative_to(root)) for p in sources]
        record['include_dirs']=[str(p.relative_to(root)) for p in includes if p.is_relative_to(root)]
        selected=set(sources)
        data_suffix={'.hex','.mem','.mif','.bin','.coe','.rom','.dat','.list','.txt'}
        for directory in set(includes+[p.parent for p in sources]):
            if directory.is_dir():
                for p in directory.iterdir():
                    if p.is_file() and p.suffix.lower() in data_suffix|{'.vh','.svh'}:
                        selected.add(p)
        for source in sources:
            if not source.is_file():
                record['errors'].append('missing_source:'+str(source));continue
            text=source.read_text(errors='replace')
            for name in set(re.findall(r'"([^"\n]+\.(?:hex|mem|mif|bin|coe|rom|dat|list|txt))"',text,re.I)):
                proposed=[source.parent/name,root/name]+[p/name for p in includes]
                found=[p for p in proposed if p.is_file() and p.resolve().is_relative_to(root.resolve())]
                if not found:found=list(root.rglob(Path(name).name))
                found=list(dict.fromkeys(p for p in found if p.is_file()))
                record['asset_references'].append({'source':str(source.relative_to(root)),
                    'literal':name,'original_matches':[str(p.relative_to(root)) for p in found]})
                selected.update(found)
        for p in sorted(selected):
            if not p.is_file():continue
            if not p.resolve().is_relative_to(root.resolve()):
                record['errors'].append('source_outside_original_root:'+str(p));continue
            raw=p.read_bytes()
            record['files'][str(p.relative_to(root))]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
            if p in certified_sources and hashlib.sha256(raw).hexdigest()!=certified_sources[p]:
                record['errors'].append('certified_hash_mismatch:'+str(p))
        out.append(record)
    print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='memlab216')
    parser.add_argument('--baseline', default='/proj/workarea/yangao/rerun_sky130hd_20261022/runs')
    parser.add_argument('--dispositions', type=Path, default=Path('design_cases/sky130_closeout_20261028/design_disposition_128.json'))
    parser.add_argument('--output', type=Path, default=Path('design_cases/api_repair_cost_20261028/full_population'))
    parser.add_argument('--local-corpus', type=Path, default=Path('/home/yangao/r2g_agent_ws/repro_n45/corpus'))
    parser.add_argument('--overwrite', action='store_true',
                        help='Explicitly replace inventory; may discard later local source recovery metadata')
    args = parser.parse_args()
    if not args.overwrite and any((args.output / name).exists() for name in
                                 ('sky130_candidates.json', 'sky130_source_inventory.json')):
        parser.error('Inventory already exists; use a new output directory or explicit --overwrite')
    rows = [r for r in json.loads(args.dispositions.read_text())['designs'] if r.get('physical_verdict_verified')]
    assert len(rows) == 80, 'Expected authoritative 80 historical physical rescues'
    code = REMOTE_READER + '\nrun(' + repr([r['design'] for r in rows]) + ',' + repr(args.baseline) + ')\n'
    result = subprocess.run(['ssh', args.host, 'python3', '-'], input=code, text=True, capture_output=True, check=True)
    exports = json.loads(result.stdout)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    candidates, inventory = [], []
    for row, export in zip(rows, exports):
        assert row['design'] == export['design']
        cid = row['design'].split('__')[0]
        case_id = 'sky130hd_' + cid
        hashes = {rel: value['sha256'] for rel, value in export['files'].items()}
        local = args.local_corpus / row['design']
        local_missing, mismatches = [], []
        for rel, expected in hashes.items():
            path = local / rel
            if not path.is_file():
                local_missing.append(rel)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                mismatches.append(rel)
        local_ready = not local_missing and not mismatches and not export['errors']
        candidate = {
            'id': case_id, 'platform': 'sky130hd', 'design': row['design'], 'top': export.get('top'),
            'input_root': str(local) if local_ready else export.get('original_root'),
            'input_host': '208' if local_ready else '216',
            'input_kind': 'original_unrepaired_baseline_sources',
            'source_manifest': str(local / 'design.json') if local_ready else None,
            'source_files': export.get('source_files', []), 'include_dirs': export.get('include_dirs', []),
            'defines': export.get('defines', []),
            'source_sha256': hashes, 'remote_original_source_hash_matches': local_ready,
            'source_hash_mismatches': mismatches, 'baseline_reference_key': ['sky130hd', cid],
            'baseline_remote_run': export.get('baseline_remote_run'),
            'historical_category': row['category'],
            'expected_generic_recipe': row.get('attempted_strategies', []), 'preinject_recipe': False,
            'priority': 2, 'semantic_review': row.get('semantic_review'),
            'historical_acceptance_audit': row.get('acceptance_audit'),
            'input_ready': not export['errors'], 'local_208_ready': local_ready,
            'input_blockers': export['errors'], 'local_missing_files': local_missing,
            'original_asset_references': export.get('asset_references', []),
            'original_unresolved_asset_references': [a for a in export.get('asset_references', [])
                                                       if not a['original_matches']],
            'input_ready_definition': 'Original compilation sources available; unresolved historical ROM references are retained as repair challenges, not silently replaced.',
            'protocol_note': 'Original baseline source bytes only. For input_host 216, prepare and execute on 216; bulk source transfer to 208 requires explicit approval. Never pass evaluator recipe/category metadata to model. Current platform defaults; compiler transform review remains separate.',
        }
        candidates.append(candidate)
        export.update(id=case_id, historical_disposition=row, local_208_ready=local_ready,
                      local_missing_files=local_missing, local_hash_mismatches=mismatches)
        inventory.append(export)
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    save('sky130_candidates.json', {'schema': 'api-repair-cost-candidates-v1',
        'purpose': 'All 80 historically verified SKY130 repair designs through real Agent API',
        'candidates': candidates})
    save('sky130_source_inventory.json', {'host': args.host, 'baseline': args.baseline,
        'total': len(candidates), 'source_contents_transferred': False, 'records': inventory})
    print(json.dumps({'total': len(candidates),
        'ready_on_original_host': sum(c['input_ready'] for c in candidates),
        'local_208_complete': sum(c['local_208_ready'] for c in candidates),
        'blocked': {c['id']: c['input_blockers'] for c in candidates if not c['input_ready']},
        'manifest': str(output / 'sky130_candidates.json')}, indent=2))


if __name__ == '__main__':
    main()
