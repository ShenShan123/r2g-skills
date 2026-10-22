#!/usr/bin/env python3
"""Read-only review of every LC20 native qualification; preserve all denominators.

Writes a separate review and prospective envelopes, never repairs, replaces a
candidate, edits a raw receipt, or grants a learning/runtime release.
"""
import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import re
import time

CHECKS = ('A1_flow_complete', 'A2_drc_full_deck', 'A3_lvs', 'A4_route', 'A5_antenna',
          'A6_setup', 'A7_hold', 'A8_drv_limits', 'A9_task_clock', 'A10_rcx', 'A11_provenance')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def classify(final, acceptance, codes, stages, inputs_ok):
    if not inputs_ok:
        return 'ACCOUNTING_OR_INPUT_REVIEW'
    if not final:
        order = ('synth', 'floorplan', 'place', 'cts', 'route', 'finish')
        names = [s.get('stage') for s in stages]
        prefix = (bool(names) and names == list(order[:len(names)]) and
                  all(s.get('status') == 0 for s in stages[:-1]) and stages[-1].get('status') == 2)
        if not prefix:
            return 'EVIDENCE_REVIEW_REQUIRED'
        if codes and codes[-1] == 'PPL-0024' and names[-1] == 'place':
            return 'NATIVE_PIN_CAPACITY_FAILURE'
        if codes and codes[-1] == 'RSZ-0074':
            return 'TOOL_ROUTING_ERROR_HELD'
        return 'NATIVE_FAILURE_REVIEW'
    checks = acceptance.get('checks') or {}
    if (set(checks) != set(CHECKS) or acceptance.get('unknown') or
            any(c.get('result') not in ('pass', 'fail') for c in checks.values())):
        return 'EVIDENCE_REVIEW_REQUIRED'
    failed = sorted(k for k, c in checks.items() if c['result'] == 'fail')
    if failed != sorted(acceptance.get('failed') or []) or acceptance.get('accepted') != (not failed):
        return 'EVIDENCE_REVIEW_REQUIRED'
    if not failed:
        return 'INITIAL_PASS_CONTROL'
    if 'A3_lvs' in failed:
        return 'NATIVE_LVS_FAILURE_REVIEW'
    return 'VERIFIED_INITIAL_SIGNOFF_FAILURE'


def with_shared_hook(manifest, hook):
    """Permit only the existing common catalogue hook, not arbitrary executable Tcl."""
    if not hook.is_file():
        raise ValueError('Shared executor hook missing')
    m = copy.deepcopy(manifest)
    m['allowlist']['POST_GLOBAL_PLACE_TCL'] = {'type': 'enum', 'values': [str(hook)]}
    m['immutable']['files'][str(hook)] = 'sha256:' + digest(hook)
    m['catalogue_permission_amendment'] = {
        'reason': 'Existing common lvs_port_feedthrough_buffer action was absent from task allowlist',
        'applies_to': 'all LEARN/PROBE snapshots, both frozen M0 and GROW',
        'initial_config_unchanged': True, 'no_initial_repair': True,
        'not_a_waiver_of_native_lvs': True}
    return m


def review(qualification, output):
    if output.exists():
        raise RuntimeError('Never overwrite an earlier qualification review')
    started, cpu = time.time(), time.process_time()
    reg = load(qualification / 'registration.json')
    if len(reg['tasks']) != 28 or load(qualification / 'progress.json')['completed'] != 28:
        raise ValueError('All 28 original qualifications are required')
    for group in ('files', 'runtime_files', 'original_inputs'):
        for name, sha in reg[group].items():
            if not Path(name).is_file() or digest(name) != sha:
                raise ValueError('Input drift: ' + name)
    output.mkdir(parents=True)
    (output / 'envelopes').mkdir()
    hook = Path(reg['runtime_root']) / 'code/r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl'
    hashes = {str(qualification / 'registration.json'): digest(qualification / 'registration.json'),
              str(Path(__file__).resolve()): digest(__file__)}
    rows = []
    for task in reg['tasks']:
        path = qualification / (task['design'] + '.json')
        raw = load(path)
        project = Path(task['project'])
        runs = sorted((project / 'backend').glob('RUN_*'))
        events_path = project / 'reports/resource_ledger.jsonl'
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
        stages_path = runs[0] / 'stage_log.jsonl' if len(runs) == 1 else None
        stages = [json.loads(l) for l in stages_path.read_text().splitlines() if l.strip()] if stages_path else []
        log_path = runs[0] / 'flow.log' if len(runs) == 1 else None
        log = log_path.read_text(errors='replace') if log_path else ''
        codes = re.findall(r'\[ERROR ([A-Z]+-\d+)\]', log)
        accounting = raw.get('accounting') or {}
        envelope = project / 'task-envelope.json'
        ok = (accounting.get('passed') is True and accounting.get('flows') == 1 and
              accounting.get('llm_requests') == 0 and accounting.get('repair_attempts') == 0 and len(runs) == 1 and
              sum(e.get('type') == 'flow_run' for e in events) == 1 and
              not any(e.get('type') in ('repair_candidate', 'llm_request') for e in events) and
              digest(project / 'constraints/config.mk') == task['config_sha256'] and
              digest(project / 'constraints/constraint.sdc') == task['sdc_sha256'] and envelope.is_file())
        if envelope.is_file():
            m = load(envelope)
            ok = ok and m['expected_clock_period_ns'] == 5 and m['platform'] == 'sky130hd'
            ok = ok and all(m['area'][k]['max_rel_increase'] == limit
                            for k, limit in {'stdcell': .55, 'core': 1., 'die': 1.}.items())
            for spec in m['area'].values():
                ok = ok and digest(spec['reference_file']) == spec['reference_sha256'] and spec['ref'] > 0
        disposition = classify(raw.get('final_layout'), raw.get('acceptance') or {}, codes, stages, ok)
        row = {'design': task['design'], 'design_id': task['design_id'], 'group_id': task['group_id'],
               'role': task['role'], 'order': task['order'], 'disposition': disposition,
               'original_status': raw['status'], 'failed': (raw.get('acceptance') or {}).get('failed', []),
               'unknown': (raw.get('acceptance') or {}).get('unknown', []), 'native_error_codes': codes,
               'raw_receipt': str(path), 'raw_receipt_sha256': digest(path), 'new_eda': 0, 'new_llm': 0,
               'repair_dispatch_allowed': False, 'io_scope': 'internal sequential timing; supplied IO delays absent'}
        if disposition == 'NATIVE_PIN_CAPACITY_FAILURE':
            match = re.search(r'Increase the die perimeter from ([0-9.]+)um to ([0-9.]+)um', log)
            if match:
                before, after = map(float, match.groups())
                row['perimeter'] = {'before_um': before, 'suggested_um': after,
                                    'square_same_aspect_area_ratio': (after / before) ** 2,
                                    'not_a_general_area_infeasibility_proof': True}
            row['reason'] = 'Clean synth-to-place prefix; native PPL-0024; strict area caps remain enforced'
        elif disposition == 'TOOL_ROUTING_ERROR_HELD':
            row['reason'] = 'RSZ-0074 route-tree consistency error is not verified congestion or action infeasibility'
        elif disposition == 'NATIVE_LVS_FAILURE_REVIEW':
            lvs = load(project / 'lvs/netgen_lvs_result.json')
            report = project / 'lvs/netgen_lvs.rpt'
            text = report.read_text(errors='replace')
            source = list((project / 'rtl').rglob('Stage_MEM.sv'))
            aliases = []
            for f in source:
                aliases.extend(re.findall(r'assign\s+(\w+)\s*=\s*(\w+)\s*;', f.read_text()))
                hashes[str(f)] = digest(f)
            row['lvs_review'] = {'native_status': lvs.get('status'), 'mismatch_class': lvs.get('mismatch_class'),
                'native_report': str(report), 'report_sha256': digest(report), 'port_alias_assignments': aliases,
                'device_classes_equivalent': 'Device classes Stage_MEM and Stage_MEM are equivalent.' in text,
                'native_top_pin_failure': 'Final result: Top level cell failed pin matching.' in text,
                'cause': 'Port-alias representation is strongly supported; not a verified repaired native LVS',
                'common_catalogue_hook_available': hook.is_file(), 'native_failure_not_waived': True}
            hashes[str(project / 'lvs/netgen_lvs_result.json')] = digest(project / 'lvs/netgen_lvs_result.json')
            hashes[str(report)] = digest(report)
        if envelope.is_file():
            m = with_shared_hook(load(envelope), hook)
            m.update(qualification_status=disposition, formal_launch_allowed=False, repair_dispatch_allowed=False)
            dest = output / 'envelopes' / (task['design'] + '.json')
            save(dest, m)
            row.update(prospective_envelope=str(dest), prospective_envelope_sha256=digest(dest))
        for f in (path, events_path, stages_path, log_path, envelope):
            if f is not None and f.is_file():
                hashes[str(f)] = digest(f)
        rows.append(row)
    held = [r['design'] for r in rows if r['disposition'] in
            ('TOOL_ROUTING_ERROR_HELD', 'NATIVE_FAILURE_REVIEW', 'EVIDENCE_REVIEW_REQUIRED', 'ACCOUNTING_OR_INPUT_REVIEW')]
    result = {'schema': 'learning-pilot20-qualification-review-v1', 'reviewed_at': time.time(),
              'all_original_candidates_retained': True, 'candidates': 28, 'rows': rows,
              'counts': dict(collections.Counter(r['disposition'] for r in rows)), 'held_tasks': held,
              'repair_dispatch_allowed': False, 'runtime_freeze_required': True,
              'lvs_permission_validation_required': True, 'no_automatic_replacement': True,
              'input_hashes': hashes, 'new_eda_flows': 0, 'new_llm_requests': 0,
              'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu}
    save(output / 'review.json', result)
    print(json.dumps({k: result[k] for k in ('counts', 'held_tasks', 'repair_dispatch_allowed')}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qualification', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    review(args.qualification.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
