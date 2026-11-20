"""Collect AES128 evidence; incomplete DRC and formal runs never imply a pass."""
from pathlib import Path
import hashlib
import json
import re

project = Path(__file__).resolve().parent
root = project.parent
run = sorted((project / 'backend').glob('RUN_*'))[-1]
read_json = lambda path: json.loads(path.read_text())
functional = read_json(project / 'reports/functional_validation.json')
assert 'PASS 35 AES128 vectors' in (project / 'reports/rtl_sim.log').read_text()
assert 'PASS 35 AES128 vectors' in (project / 'reports/gate_sim.log').read_text()
assert functional['gate_sim_pass'] and functional['formal_status'] == 'NOT PROVED'
ppa = read_json(project / 'reports/ppa.json')
resources = read_json(project / 'reports/flow_resources.json')
reference = read_json(project / 'reports/reference_validation.json')
lvs_dir = root / 'calibre_evidence/multi_aes_property65536/lvs'
lvs = read_json(lvs_dir / 'invocation.json')
assert lvs['result'] == 'CORRECT' and reference['status'] == 'PASS'
assert 'property resolution maximum exceeded' not in (lvs_dir / 'run.log').read_text().lower()
attempt_names = ['multi_aes', 'multi_aes_flattened_turbo', 'multi_aes_fin_merged',
                 'multi_aes_feol_merged', 'multi_aes_feol_hier']
attempts = []
for name in attempt_names:
    path = root / 'calibre_evidence' / name / 'drc'
    receipt = read_json(path / 'invocation.json')
    text = (path / 'run.log').read_text()
    progress = re.findall(r'OPS COMPLETE\s*=\s*(\d+)\s+OF\s+(\d+)', text)
    attempts.append({'name': name, 'evidence_directory': str(path), **receipt,
                     'last_operation_progress': list(map(int, progress[-1])) if progress else None,
                     'completed_drc_report_present': (path / 'drc_report').exists()})
assert all(a['process_exit'] == 124 and not a['completed_drc_report_present'] for a in attempts)
result = {
    'design': 'aes_cipher_top',
    'description': 'Full AES128 encryption core including key expansion',
    'status': 'PHYSICAL_FLOW_AND_LVS_PASS; FULL_DRC_INCOMPLETE; FORMAL_NOT_PROVED',
    'project': str(project), 'run': run.name, 'logic_blackboxes': [],
    'rtl_functional': {'result': 'PASS', 'vectors': 35,
                       'reference': 'NIST FIPS197 C.1 and OpenSSL AES128 ECB',
                       'log': str(project / 'reports/rtl_sim.log')},
    'gate_functional_and_formal': functional,
    'drc': {'status': 'INCOMPLETE_TIMEOUT', 'clean': None, 'violation_count': None,
            'attempted_official_rule_checks': 334, 'rule_exclusions': [],
            'maximum_results': 'ALL', 'timeout_s_per_attempt': 600,
            'interpretation': 'No completed DRC report; no clean or violation verdict can be inferred.',
            'attempts': attempts},
    'lvs': {**lvs, 'evidence_directory': str(lvs_dir),
            'property_resolution_exceeded_warning': False},
    'reference_validation': reference,
    'original_geometry': read_json(root / 'multi_aes_input/geometry_verification.json'),
    'last_drc_geometry': read_json(root / 'multi_aes_feol_hier_input/geometry_verification.json'),
    'ppa': ppa, 'flow_resources': resources,
}
(project / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
files = []
for name in attempt_names + ['multi_aes_property65536']:
    files += list((root / 'calibre_evidence' / name).rglob('*'))
for directory in [root / 'multi_aes_input', root / 'multi_aes_flattened_input',
                  root / 'multi_aes_fin_merged_input', root / 'multi_aes_feol_merged_input',
                  root / 'multi_aes_feol_hier_input', project / 'rtl',
                  project / 'constraints', project / 'reports']:
    files += list(directory.glob('*'))
files += list(project.glob('*.py')) + list(project.glob('*.json'))
files += list((project / 'tb').glob('*.v')) + list((project / 'tb').glob('*.json'))
files += list((project / 'tb').glob('*.eqy')) + list((project / 'tb').glob('*.ys'))
files += list((project / 'tb').glob('*.py'))
files += list((run / 'logs').glob('*.log')) + list((run / 'logs').glob('*.json'))
files += [project / 'RESULT.md', run / 'results/6_final.gds', run / 'results/6_final.v',
          run / 'results/6_final.def', run / 'results/6_final.sdc', run / 'run-meta.json',
          root / 'drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef',
          root / 'drc_diagnosis/asap7sc7p5t_R_edge_pin_window_candidate.lef',
          root / 'run_check.py', root / 'prepare_design_gds.py', root / 'prepare_variant_cdl.py',
          root / 'verify_geometry.py', root / 'validate_design_reference.py']
manifest = {}
for path in sorted(set(files)):
    if not path.is_file() or path.name == 'evidence_manifest.json':
        continue
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    manifest[str(path.relative_to(root))] = {'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}
(project / 'evidence_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'artifacts_hashed': len(manifest),
                  'drc_attempts': len(attempts), 'lvs': lvs['result'],
                  'flow_elapsed_s': resources['orfs_stage_elapsed_sum_s']}, indent=2))
