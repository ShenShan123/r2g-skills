"""Bind the multi-design result table to preserved inputs and checker receipts."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CASES = [
    ('UART', 'multi_uart_strict', 'multi_uart_strict/signoff/drc',
     'multi_uart_strict/signoff/lvs'),
    ('AES-128', 'multi_aes', 'calibre_evidence/multi_aes/drc',
     'calibre_evidence/multi_aes_property65536/lvs'),
    ('PicoRV32', 'multi_picorv32', 'calibre_evidence/multi_picorv32/drc',
     'calibre_evidence/multi_picorv32_property65536/lvs'),
]

def read(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def seconds(value):
    result = 0.0
    for field in value.split(':'):
        result = result * 60 + float(field)
    return result

files = {Path(__file__).resolve(), ROOT / 'REPORT.md', ROOT / 'MULTI_DESIGN_REPORT.md',
         ROOT / 'qualification.json', ROOT / 'validate_design_reference.py',
         ROOT / 'prepare_design_gds.py', ROOT / 'multi_analysis/strict_via_binary.json',
         ROOT / 'reference_diagnosis/strict_via_in_pin.patch',
         ROOT / 'drc_diagnosis/performance_audit/REPORT.md',
         ROOT / 'drc_diagnosis/performance_audit/rule_level_audit.json',
         ROOT / 'drc_diagnosis/performance_audit/evidence_manifest.json'}
cases = []
for label, project, drc_rel, lvs_rel in CASES:
    folder = ROOT / project
    inp = ROOT / (project + '_input')
    drc_dir, lvs_dir = ROOT / drc_rel, ROOT / lvs_rel
    drc, lvs = read(drc_dir / 'invocation.json'), read(lvs_dir / 'invocation.json')
    for kind, receipt in [('drc', drc), ('lvs', lvs)]:
        assert receipt['gds_sha256'] == sha(inp / (kind + '.gds'))
        deck = ROOT / f'private_rules/calibredecks_r1p7/calibre/ruledirs/{kind}/{kind}Rules_calibre_asap7.rul'
        assert receipt['deck_sha256'] == sha(deck)
    assert lvs['source_sha256'] == sha(inp / 'source.cdl')
    assert lvs['process_exit'] == 0 and lvs['result'] == 'CORRECT'
    assert 'LVS completed. CORRECT.' in (lvs_dir / 'run.log').read_text()
    assert 'property resolution maximum exceeded' not in (lvs_dir / 'mask.lvs.rep').read_text().lower()
    assert 'DRC MAXIMUM RESULTS ALL' in (drc_dir / 'run.svrf').read_text()
    if drc['process_exit'] == 0:
        report = (drc_dir / 'drc_report').read_text()
        rules = re.findall(r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)', report, re.M)
        assert len(rules) == drc['rulechecks'] == 334
        assert sum(int(count) for _, count in rules) == drc['results']
        files.add(drc_dir / 'drc_report')
    else:
        assert drc['process_exit'] == 124  # The preserved bounded runs timed out.
    geometry = read(inp / 'geometry_verification.json')
    assert geometry['unchanged']
    audit = read(folder / 'reports/reference_validation.json')
    assert audit['status'] == 'PASS' and audit['source_sha256'] == lvs['source_sha256']
    ppa = read(folder / 'reports/ppa.json')
    if project == 'multi_aes':
        # AES records final 6_report.json fields directly, in native ps.
        original_gds = Path(read(inp / 'provenance.json')['original_gds'])
        assert read(original_gds.parent.parent / 'run-meta.json')['make_status'] == 0
        timing = {'setup_wns': ppa['setup_worst_slack_ps'],
                  'hold_wns': ppa['hold_worst_slack_ps'], 'setup_tns': ppa['setup_tns_ps']}
        area = ppa['stdcell_area_um2']
    else:
        assert ppa['orfs_status'] == 'complete'
        timing = ppa['summary']['timing']
        area = ppa['geometry']['stdcell_area_um2']
    elapsed = re.findall(r'Elapsed time: ([\d:.]+)\[h:\]min:sec.*?Peak memory: (\d+)KB',
                         (folder / 'backend_driver.log').read_text())
    assert len(elapsed) == 19
    if project == 'multi_uart_strict':
        formal = read(folder / 'formal/final_verification/verification.json')
        assert formal['status'] == 'PASS'
        assert formal['final_netlist']['sha256'] == sha(Path(formal['final_netlist']['path']))
        functional = {'rtl_and_final_gate': 'PASS', 'formal': 'PASS',
                      'evidence': ['sim/simulation.log', 'sim/gate_simulation.log',
                                   'formal/final_verification/verification.json']}
        assert all('PASS' in (folder / p).read_text() for p in functional['evidence'][:2])
    elif project == 'multi_aes':
        validation = read(folder / 'reports/functional_validation.json')
        assert validation['gate_sim_pass'] and validation['vectors'] == 35
        assert 'PASS 35 AES128 vectors' in (folder / 'reports/rtl_sim.log').read_text()
        functional = {'rtl_and_final_gate': 'PASS', 'vectors': 35,
                      'formal': validation['formal_status'],
                      'evidence': ['reports/functional_validation.json']}
    else:
        for prefix in ['rtl', 'gate']:
            validation = read(folder / f'reports/{prefix}_simulation.json')
            assert validation['result'] == 'PASS'
        for filename, expected_hash in validation['inputs'].items():
            assert sha(folder / filename) == expected_hash
        functional = {'rtl_and_final_gate': 'PASS', 'memory_latency_modes': 3,
                      'formal': 'NOT RUN',
                      'evidence': ['reports/rtl_simulation.json', 'reports/gate_simulation.json']}
    cases.append({
        'design': label, 'project': project,
        'qualified_dual_clean': drc.get('result') == 'clean' and lvs['result'] == 'CORRECT',
        'drc_status': 'COMPLETE' if drc['process_exit'] == 0 else 'TIMEOUT_NO_VERDICT',
        'drc_evidence': drc_rel, 'lvs_evidence': lvs_rel, 'drc': drc, 'lvs': lvs,
        'clock_period_ns': 1.0,
        'setup_wns_ns': timing['setup_wns'] / 1000,
        'hold_wns_ns': timing['hold_wns'] / 1000,
        'setup_tns_ns': timing['setup_tns'] / 1000,
        'stdcell_area_um2': area,
        'logic_cells': audit['combinational']['instances'] + audit['sequential']['instances'],
        'sequential_cells': audit['sequential']['instances'], 'mos': audit['mos_after'],
        'timed_stage_elapsed_sum_s': round(sum(seconds(a) for a, _ in elapsed), 2),
        'timed_stage_count': len(elapsed),
        'peak_rss_mib': max(int(b) for _, b in elapsed) / 1024,
        'functional_result_file': project + '/RESULT.json',
        'functional_validation': functional,
        'reference_audit': project + '/reports/reference_validation.json',
        'geometry_verification': project + '_input/geometry_verification.json',
        'generic_ppa_drc_field_used': False,
    })
    if project == 'multi_aes':
        probe_dir = ROOT / 'calibre_evidence/asap7_accel_hyper_aes_20261116/drc'
        probe = read(probe_dir / 'invocation.json')
        assert probe['result'] == 'INCOMPLETE' and probe['completed_rulechecks'] == 328
        assert probe['gds_sha256'] == sha(ROOT / 'multi_aes_feol_hier_input/drc.gds')
        cases[-1]['additional_drc_probe'] = {
            'evidence': str(probe_dir.relative_to(ROOT)), 'receipt': probe,
            'reported_expanded_results_so_far': sum(probe['observed_violations_in_completed_rules'].values()),
            'final_violation_count': None,
            'individual_markers_reviewed': False,
        }
        files.update(path for path in probe_dir.iterdir() if path.is_file() and not path.is_symlink())
    files.update(path for path in inp.iterdir() if path.is_file())
    for directory in [drc_dir, lvs_dir, folder / 'reports', folder / 'constraints']:
        files.update(path for path in directory.rglob('*') if path.is_file() and not path.is_symlink()
                     and path.suffix in {'.json', '.log', '.txt', '.mk', '.tcl', '.sdc', '.svrf', '.rep', '.sp'})
    original_gds = Path(read(inp / 'provenance.json')['original_gds'])
    files.update({folder / 'backend_driver.log', folder / 'RESULT.md', folder / 'RESULT.json',
                  original_gds, original_gds.with_suffix('.v')})
    files.update(folder / path for path in functional['evidence'])
    for directory in [folder / 'rtl', folder / 'tb']:
        files.update(path for path in directory.iterdir() if path.is_file()
                     and path.suffix in {'.v', '.sv', '.hex', '.json', '.eqy', '.py'})

result = {
    'updated_gmt8': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'platform': 'ASAP7 r1p7 / 7.5-track RVT / FF timing corner',
    'prior_gcd_result': 'qualification.json',
    'default_platform_enabled': False,
    'production_openroad_replaced': False,
    'resource_basis': 'Sum of 19 EDA tool elapsed-time lines, excluding wrapper, journal I/O, compilation and signoff. Peak RSS is maximum, not sum.',
    'cases': cases,
}
out = ROOT / 'multi_design_results.json'
out.write_text(json.dumps(result, indent=2) + '\n')
files.add(out)
(ROOT / 'multi_design_evidence_sha256.json').write_text(json.dumps(
    {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)}, indent=2) + '\n')
print(json.dumps([{'design': case['design'], 'dual_clean': case['qualified_dual_clean'],
                   'drc': case['drc_status'], 'lvs': case['lvs']['result']} for case in cases]))
