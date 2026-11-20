"""Summarize preserved checker reports, with hashes binding reports to inputs."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re

p = argparse.ArgumentParser()
p.add_argument('--project', required=True)
p.add_argument('--evidence', required=True)
p.add_argument('--lvs-evidence', help='Optional separate LVS run under calibre_evidence')
args = p.parse_args()
r = Path(__file__).resolve().parent
project = r / args.project
inp = r / (args.project + '_input')
e = r / 'calibre_evidence' / args.evidence
le = r / 'calibre_evidence' / args.lvs_evidence / 'lvs' if args.lvs_evidence else e / 'lvs'

def read_json(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def resources(path):
    log = path.read_text()
    rss = re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)', log)
    return {'max_rss_kib': int(rss[1]) if rss else None}

drc = read_json(e / 'drc/invocation.json')
lvs = read_json(le / 'invocation.json')
for kind, receipt in [('drc', drc), ('lvs', lvs)]:
    assert receipt['process_exit'] == 0
    assert receipt['gds_sha256'] == sha(inp / (kind + '.gds'))
    deck = r / f'private_rules/calibredecks_r1p7/calibre/ruledirs/{kind}/{kind}Rules_calibre_asap7.rul'
    assert receipt['deck_sha256'] == sha(deck)
assert lvs['source_sha256'] == sha(inp / 'source.cdl')
report = (e / 'drc/drc_report').read_text()
rules = [{'rule': m[0], 'results': int(m[1]), 'expanded_results': int(m[2] or m[1])}
         for m in re.findall(r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)(?:\s*\((\d+)\))?', report, re.M)]
assert len(rules) == drc['rulechecks'] == 334
assert sum(x['results'] for x in rules) == drc['results']
assert sum(x['expanded_results'] for x in rules) == drc['expanded_results']
assert 'DRC MAXIMUM RESULTS ALL' in (e / 'drc/run.svrf').read_text()
lvs_report = (le / 'mask.lvs.rep').read_text()
assert f'LVS completed. {lvs["result"]}.' in (le / 'run.log').read_text()

def count_pairs(section):
    return {key.lower().replace(' ', '_'): [int(a), int(b)]
            for key, a, b in re.findall(r'^\s*(Ports|Nets|Total Inst):\s+(\d+)\s+(\d+)', section, re.M)}

initial, after = lvs_report.split('INITIAL NUMBERS OF OBJECTS', 1)[1].split('NUMBERS OF OBJECTS AFTER TRANSFORMATION', 1)
after = after.split('INFORMATION AND WARNINGS', 1)[0]
ppa = read_json(project / 'reports/ppa.json')
driver = (project / 'backend_driver.log').read_text()
elapsed = re.findall(r'Elapsed time: ([\d:.]+)\[h:\]min:sec.*?Peak memory: (\d+)KB', driver)

def seconds(value):
    total = 0.0
    for piece in value.split(':'):
        total = total * 60 + float(piece)
    return total

timing = ppa['summary']['timing']
source_provenance = read_json(inp / 'source_provenance.json')
geometry = read_json(inp / 'geometry_verification.json')
assert geometry['unchanged']
formal_path = r / 'lvs_diagnosis/formal' / args.project / Path(ppa['run_dir']).name / 'verification.json'
formal = read_json(formal_path)
assert formal['status'] == 'PASS'
assert formal['post_route_netlist']['sha256'] == sha(Path(formal['post_route_netlist']['path']))
config = (project / 'constraints/config.mk').read_text()
adaptation_paths = {name: Path(re.search(rf'export {name}\s*=\s*(.+)', config)[1])
                    for name in ['TECH_LEF', 'SC_LEF', 'PDN_TCL']}
summary = {
    'created_gmt8': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'platform': 'asap7', 'design': 'gcd', 'project': args.project,
    'evidence': str(e.relative_to(r)),
    'lvs_evidence': str(le.relative_to(r)),
    'result_scope': 'single 16-bit GCD smoke design',
    'default_platform_enabled': False,
    'qualified_dual_clean': drc['result'] == 'clean' and lvs['result'] == 'CORRECT',
    'archive_sha256': read_json(r / 'archive_manifest.json')['archive_sha256'],
    'flow': {
        'status': ppa['orfs_status'], 'clock_period_ns': 1.0,
        'setup_wns_ns': timing['setup_wns'] / 1000,
        'setup_tns_ns': timing['setup_tns'] / 1000,
        'hold_wns_ns': timing['hold_wns'] / 1000,
        'stdcell_area_um2': ppa['geometry']['stdcell_area_um2'],
        'die_area_um2': ppa['geometry']['die_area_um2'],
        'timed_stage_elapsed_sum_s': round(sum(seconds(a) for a, b in elapsed), 2),
        'timed_stage_count': len(elapsed),
        'peak_rss_kib': max(int(b) for a, b in elapsed),
        'resource_note': 'Sum of explicit Elapsed time log lines; excludes wrapper overhead. Peak RSS is maximum, not sum.'
    },
    'drc': {**drc, **resources(e / 'drc/run.log'), 'result_limit': 'ALL', 'rules_excluded': []},
    'lvs': {**lvs, **resources(le / 'run.log'),
            'initial_counts_layout_source': count_pairs(initial),
            'after_transformation_counts_layout_source': count_pairs(after),
            'warnings': re.findall(r'^\s*Warning:\s*(.*)', lvs_report, re.M),
            'logic_blackboxes': source_provenance['logic_blackboxes'],
            'reference_from_tested_layout': source_provenance['reference_from_tested_layout']},
    'geometry_verification': str((inp / 'geometry_verification.json').relative_to(r)),
    'formal_verification': str(formal_path.relative_to(r)),
    'adaptation_sha256': {name: sha(path) for name, path in adaptation_paths.items()},
    'limitations': ['One small design does not qualify every design or library corner.',
                    'Calibre xACT RCX and large-design power integrity are not qualified.']
}
(r / 'qualification.json').write_text(json.dumps(summary, indent=2) + '\n')
files = [r / 'archive_manifest.json', r / 'qualification.json', r / 'summarize_qualification.py',
         r / 'run_check.py', r / 'prepare_variant_cdl.py', r / 'prepare_variant_gds.py',
         r / 'verify_geometry.py', project / 'reports/ppa.json', project / 'backend_driver.log',
         project / 'constraints/config.mk', Path(read_json(inp / 'provenance.json')['original_gds'])]
files += [x for x in inp.iterdir() if x.is_file()]
files += [x for x in e.rglob('*') if x.is_file() and not x.is_symlink()]
files += [x for x in le.rglob('*') if x.is_file() and not x.is_symlink()]
files += [r / 'lvs_diagnosis/asap7_R_official_pex_full.cdl', r / 'lvs_diagnosis/full_library_provenance.json']
files += list(adaptation_paths.values())
files += [x for x in formal_path.parent.iterdir() if x.is_file()]
files += [r / 'REPORT.md', r / 'REPRODUCE.md', Path(formal['rtl']['path']),
          Path(formal['post_route_netlist']['path']),
          r / 'drc_diagnosis/edge_pin_window_geometry_verification.json',
          r / 'drc_diagnosis/edge_pin_window_provenance.json',
          r / 'lvs_diagnosis/combinational_truth_verification.json',
          r / 'lvs_diagnosis/sequential_topology_verification.json',
          r / 'lvs_diagnosis/physical_audit' / (args.project + '.json')]
(r / 'evidence_sha256.json').write_text(json.dumps({str(x.relative_to(r)): sha(x) for x in sorted(set(files))}, indent=2) + '\n')
print(json.dumps({'project': args.project, 'drc': drc['results'], 'lvs': lvs['result'],
                  'dual_clean': summary['qualified_dual_clean']}))
