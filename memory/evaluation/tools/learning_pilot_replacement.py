#!/usr/bin/env python3
"""One auditable LEARN replacement, before any learning repair outcome exists.

Same-class reserve was exhausted: keep its original order, then DRC, then timing.
Never draw from PROBE, change its roster, or silently advance after a new flow.
This amendment authorizes one no-model qualification, not repair dispatch.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

import learning_pilot_qualification as Q

HELD = 'd_006ad63884b413ced2a3'
CLOCK_INVALID = 'd_1284090c399282b47859'
MARKERS = re.compile(r'picorv32|\bibex\b|lowrisc|secworks|ultraembedded|alexforencich|verilog[-_]ethernet', re.I)


def reserve_order(roster, inventory):
    used = {x['group_id'] for rows in roster['selected'].values() for x in rows}
    return [(s, i) for s in ('physical', 'drc', 'timing')
            for i in roster['qualification_reserve_order']['LEARN'][s]
            if inventory[i]['group_id'] not in used]


def replace_row(roster, row):
    result = copy.deepcopy(roster)
    positions = [i for i, old in enumerate(result['selected']['LEARN']) if old['design_id'] == HELD]
    if len(positions) != 1 or row['role'] != 'LEARN':
        raise ValueError('Expected exactly one held LEARN position')
    pos = positions[0]
    result['selected']['LEARN'][pos] = {**row, 'order': pos + 1}
    Q.selected_rows(result)
    assert result['selected']['PROBE'] == roster['selected']['PROBE']
    return result


def amend(roster_path, output):
    if output.exists():
        raise RuntimeError('Existing amendment; no overwrite')
    started, cpu = time.time(), time.process_time()
    roster = Q.load(roster_path)
    Q.selected_rows(roster)
    Q.verify_hashes(roster['input_hashes'])
    inventory_path, = [Path(p) for p in roster['input_hashes'] if Path(p).name == 'candidate_inventory.jsonl']
    inventory = {x['design_id']: x for x in map(json.loads, inventory_path.read_text().splitlines())}
    csv_path, = [Path(p) for p in roster['input_hashes'] if Path(p).name == 'inventory_sky130hd.csv']
    with csv_path.open() as stream:
        baseline = list(csv.DictReader(stream))
    hashes = {str(roster_path): Q.digest(roster_path)}
    rejected, chosen = [], None
    order = reserve_order(roster, inventory)
    for stratum, ident in order:
        item = inventory[ident]
        path = Path(item['directory']) / 'DESIGN.json'
        manifest = Q.load(path)
        hashes[str(path)] = Q.digest(path)
        if hashes[str(path)] != item['design_manifest_sha256']:
            raise ValueError('Candidate manifest drift')
        texts = []
        for entry in manifest['source_files']:
            source = (path.parent / entry['file']).resolve()
            Q.inside(Path(roster['source_corpus']) / 'sources', source)
            hashes[str(source)] = Q.digest(source)
            if hashes[str(source)] != entry['sha256']:
                raise ValueError('Candidate RTL drift')
            texts.append(source.read_text(errors='replace'))
        design = Path(item['directory']).name
        rows = [r for r in baseline if r['design'] == design and r['platform'] == 'sky130hd' and r['period'] == '5']
        if len(rows) != 1:
            raise ValueError('Ambiguous/missing baseline row')
        b = rows[0]
        reason = None
        if ident == CLOCK_INVALID:
            reason = 'CLOCK_ENABLE is an enable; sequential clocks are INPUT_CLK/OUTPUT_CLK, not the recorded clock'
        elif item['split_reservation'] != 'LEARN_CANDIDATE' or b['split'] != 'dev':
            reason = 'Not an existing LEARN-reserved, CSV-development candidate'
        elif item['frontend'] != 'verilog-native' or b['clock_kind'] != 'real':
            reason = 'Unsupported frontend/clock metadata'
        elif any(MARKERS.search(text) for text in texts):
            reason = 'Known SOURCE/DEV family marker; conservative hold'
        elif not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', b['clock_port'] or '') or not any(
                re.search(r'\b(?:posedge|negedge)\s+' + re.escape(b['clock_port']) + r'\b', text) for text in texts):
            reason = 'Recorded clock has no direct sequential-edge evidence in source closure; manual review required'
        if reason:
            rejected.append({'design_id': ident, 'design': design, 'stratum': stratum, 'reason': reason,
                             'new_flows': 0})
            continue
        run = Path(roster['raw_baseline_root']) / b['run_dir']
        for p in (run / 'config.mk', run / 'constraint.sdc'):
            hashes[str(p)] = Q.digest(p)
        chosen = {'design': design, 'design_id': ident, 'design_manifest': str(path),
                  'frontend': item['frontend'], 'group_id': item['group_id'],
                  'repository': item['repository'], 'source_commit': item['commit'],
                  'source_closure_sha256': hashlib.sha256(json.dumps(sorted(item['source_hashes'])).encode()).hexdigest(),
                  'source_file_count': len(item['source_hashes']), 'source_hashes_verified': True,
                  'initial_config': str(run / 'config.mk'), 'initial_constraints': str(run / 'constraint.sdc'),
                  'baseline_run_dir': str(run), 'baseline_clock_port': b['clock_port'],
                  'baseline_status': b['status'], 'baseline_failure_class': b['failure_class'],
                  'baseline_cells': b['cells'], 'baseline_seconds': b['seconds'],
                  'role': 'LEARN', 'stratum': stratum, 'physical_qualification': 'PENDING'}
        break
    if chosen is None:
        raise RuntimeError('No eligible candidate; do not broaden scope automatically')
    result = replace_row(roster, chosen)
    output.mkdir(parents=True)
    (output / 'operator').mkdir()
    shutil.copy2(__file__, output / 'operator' / Path(__file__).name)
    shutil.copy2(Q.__file__, output / 'operator' / Path(Q.__file__).name)
    for p in (output / 'operator').iterdir():
        hashes[str(p)] = Q.digest(p)
    result['input_hashes'].update(hashes)
    result['qualification_amendment'] = {
        'user_decision': 'Select one from existing LEARN pool by fixed rule; qualify first; preserve 20 valid learning designs',
        'held_design_id': HELD, 'held_reason': 'Q1 native router internal error RSZ-0074; raw result retained',
        'rule': 'Unselected disjoint existing LEARN reserve order: physical, then DRC, then timing; first source/clock-eligible candidate',
        'ordered_candidates': [{'stratum': s, 'design_id': i} for s, i in order],
        'preflow_rejections': rejected, 'replacement_design': chosen['design'],
        'stratum_change': 'physical -> ' + chosen['stratum'], 'probe_unchanged': True,
        'new_qualification_flows_authorized': 1, 'new_model_requests_authorized': 0,
        'repair_dispatch_allowed': False, 'selection_uses_TEHM_repair_outcomes': False,
        'clock_review_scope': 'Recorded literal clock directly drives source sequential edges; native qualification still required',
        'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu}
    Q.save(output / 'roster-r2.json', result)
    print(json.dumps(result['qualification_amendment'], indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--roster', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    amend(a.roster.resolve(), a.output.resolve())
