#!/usr/bin/env python3
"""LC20 immutable growth versions and neutral capture. Not a paid dispatcher.

Every task reads one sealed version. A completed LEARN creates a new version;
only an atomic head publication makes it visible. Failed builds and all costs
remain auditable. PROBE cannot commit. Use the final private runtime on sys.path.
"""
import collections
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as stream:
        stream.write(stable(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def verify(hashes):
    for name, sha in hashes.items():
        if not Path(name).is_file() or digest(name) != sha:
            raise ValueError('Input drift: ' + name)


def _lock(root):
    stream = (root / 'growth.lock').open('a')
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return stream


def _stats(conn):
    from tehm import evidence as E
    from tehm.retrieval.index import build_index
    index = build_index(conn, lifecycle_statuses=frozenset({'candidate', 'promoted'}))
    admitted = []
    own_positive = set()
    for rid in sorted(index.rule_ids()):
        status = conn.execute('SELECT status FROM tehm_rule_status WHERE rule_id=?', (rid,)).fetchone()
        profile = E.rule_evidence(conn, rid)
        if not E.admissible(profile, status[0] if status else None,
                            require_verified_positive=True, caliber_name='main'):
            continue
        admitted.append([rid, index.rules[rid], profile])
        tids = set()
        for (raw,) in conn.execute('SELECT source_substitution_json FROM tehm_rule_sources WHERE rule_id=?', (rid,)):
            tids.update(json.loads(raw or '{}'))
        for tid in tids:
            row = conn.execute('SELECT outcome,verifier_json,observation_delta_json FROM tehm_transitions '
                               'WHERE transition_id=?', (tid,)).fetchone()
            if row and E.classify(row[0], json.loads(row[1]), json.loads(row[2])) == 'positive':
                own_positive.update(r[0] for r in conn.execute('SELECT record_id FROM tehm_attempts WHERE transition_id=?', (tid,)))
    attempt_ids = sorted(r[0] for r in conn.execute('SELECT record_id FROM tehm_attempts'))
    # Exclude time-varying utility metadata from the semantic projection.
    projection = [[r, v['before_pattern'], v['after_pattern'], v['hard_preconditions'], p] for r, v, p in admitted]
    return {'canonical_units': len(attempt_ids), 'attempt_ids': attempt_ids,
            'rules': conn.execute('SELECT count(*) FROM tehm_rules').fetchone()[0],
            'admitted_rules': len(admitted), 'admitted_rule_ids': [r[0] for r in admitted],
            'positive_own_admitted_ids': sorted(own_positive),
            'executable_sha256': hashlib.sha256(stable(projection).encode()).hexdigest()}


def _store_stats(store):
    from tehm import db
    c = db.connect_read_only(store / 'tehm.sqlite')
    try:
        return _stats(c)
    finally:
        c.close()


def initialize(root, m0, learn_order, probes):
    from tehm.snapshot import bind_snapshot, seal_store
    root, m0 = Path(root), Path(m0)
    if root.exists():
        raise RuntimeError('Existing growth state; no overwrite')
    if (len(set(learn_order)) != len(learn_order) or len(set(probes)) != len(probes)
            or set(learn_order) & set(probes)):
        raise ValueError('LEARN/PROBE scope is not disjoint and unique')
    started, cpu = time.time(), time.process_time()
    root.mkdir(parents=True)
    version = root / 'versions' / 'v000'
    bind_snapshot(m0, version)
    stats = _store_stats(version)
    sha = seal_store(version)
    head = {'schema': 'learning-growth-head-v1', 'version': 'v000', 'store': str(version),
            'db_sha256': sha, 'learn_order': list(learn_order), 'probe_ids': list(probes),
            'committed_learn': [], 'initial_store': str(m0), 'initial_db_sha256': digest(m0 / 'tehm.sqlite'),
            'first_admitted_unit_ids': stats['positive_own_admitted_ids'], 'stats': stats,
            'experienced_learn_designs': 0, 'new_canonical_units': 0,
            'created_at': time.time(), 'initial_memory_is_empty': stats['canonical_units'] == 0}
    save(root / 'head.json', head)
    _maintenance(root, {'operation': 'initialize', 'status': 'COMMITTED',
                        'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu,
                        'llm_requests': 0, 'tokens': 0})
    return head


def _maintenance(root, value):
    with (root / 'maintenance.jsonl').open('a') as stream:
        stream.write(stable({**value, 'recorded_at': time.time()}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def bind(root, task, role, destination):
    from tehm.snapshot import bind_snapshot, seal_store
    root, destination = Path(root), Path(destination)
    with _lock(root):
        head = load(root / 'head.json')
        expected = head['learn_order'] if role == 'LEARN' else head['probe_ids'] if role == 'PROBE' else []
        if task not in expected:
            raise ValueError('Task/role outside registered scope')
        if digest(Path(head['store']) / 'tehm.sqlite') != head['db_sha256']:
            raise ValueError('Committed store drift')
        if role == 'LEARN' and (len(head['committed_learn']) >= len(head['learn_order']) or
                                task != head['learn_order'][len(head['committed_learn'])]):
            raise ValueError('Only next LEARN may bind the current head')
        if destination.exists() or destination.with_name(destination.name + '.binding').exists():
            raise FileExistsError('Existing or partial task binding; no implicit retry')
        started, cpu = time.time(), time.process_time()
        record = bind_snapshot(Path(head['store']), destination)
        record.update(task=task, role=role, growth_version=head['version'],
                      experienced_learn_designs=head['experienced_learn_designs'],
                      source_db_sha256=head['db_sha256'])
        save(destination / 'growth-binding.json', record)
        seal_store(destination)
        _maintenance(root, {'operation': 'bind', 'task': task, 'role': role, 'version': head['version'],
                            'status': 'COMMITTED', 'wall_s': time.time() - started,
                            'cpu_s': time.process_time() - cpu, 'llm_requests': 0, 'tokens': 0})
        return record


def commit(root, task, bundle):
    from tehm import neutral_export as N
    from tehm.snapshot import bind_snapshot, seal_store
    from tehm_backend import TehmMemoryBackend
    root = Path(root)
    with _lock(root):
        head = load(root / 'head.json')
        n = len(head['committed_learn'])
        if n >= len(head['learn_order']) or task != head['learn_order'][n]:
            raise ValueError('Only the next LEARN task may commit')
        if bundle.get('task') != task or bundle.get('role') != 'LEARN':
            raise ValueError('Episode scope is not the next LEARN task')
        if bundle.get('episode_status') not in ('ACCEPTED', 'NOT_ACCEPTED', 'UNVERIFIED_OR_SAFETY_STOP'):
            raise ValueError('Episode must be terminal')
        if bundle.get('capture_complete') is not True:
            raise ValueError('Incomplete action capture')
        binding = bundle.get('task_start_binding')
        if binding:
            if (binding.get('growth_version') != head['version'] or binding.get('source_db_sha256') != head['db_sha256']
                    or binding.get('role') != 'LEARN' or binding.get('task') != task):
                raise ValueError('Task-start snapshot is not the current committed parent')
        elif not bundle.get('diagnostic_fixture') and not bundle.get('diagnostic_replay'):
            raise ValueError('Missing task-start snapshot binding')
        verify(bundle['input_hashes'])
        units = bundle['units']
        if any(u['identity']['design'] != task or u['identity']['platform'] != 'sky130hd' for u in units):
            raise ValueError('Experience scope leaks another task/platform')
        if any(not u.get('source_eligible') for u in units):
            raise ValueError('Conflicting canonical unit cannot enter growth store')
        if digest(Path(head['store']) / 'tehm.sqlite') != head['db_sha256']:
            raise ValueError('Committed store drift')
        version = root / 'versions' / ('v%03d' % (n + 1))
        if version.exists() or version.with_name(version.name + '.binding').exists():
            raise RuntimeError('Prior partial write exists; no implicit retry')
        started, cpu = time.time(), time.process_time()
        backend = None
        outcome = 'FAILED'
        try:
            bind_snapshot(Path(head['store']), version)
            # The new directory/db are owned copies; never unseal the previous head.
            version.chmod(version.stat().st_mode | 0o200)
            dbfile = version / 'tehm.sqlite'
            dbfile.chmod(dbfile.stat().st_mode | 0o200)
            save(version / 'episode-export.json', bundle)
            backend = TehmMemoryBackend(db_path=dbfile, artifact_root=version / 'artifacts', read_only_eval=False)
            conn, artifacts = backend._open()
            import os as _os
            if _os.environ.get('TEHM_EVIDENCE_CALIBER', 'main') != 'main' or _os.environ.get('TEHM_D1_REQUIRE_VERIFIED_POSITIVE', '1') == '0':
                raise ValueError('Growth requires strict main evidence caliber and D1')
            # Canonical importer deduplicates copies and rejects conflicting content.
            receipts = N.import_units(conn, artifacts, units)
            backend.rebuild()
            stats = _stats(conn)
            if N.import_units(conn, artifacts, units):
                raise RuntimeError('Re-import was not idempotent')
            backend.rebuild()
            again = _stats(conn)
            if stats != again:
                raise RuntimeError('Repeated rebuild changed effective evidence or index')
            previous_first = set(head['first_admitted_unit_ids'])
            first = previous_first | set(stats['positive_own_admitted_ids'])
            receipt = {'task': task, 'version': version.name, 'parent_version': head['version'],
                       'experienced_learn_designs': n + 1, 'new_canonical_units': len(receipts),
                       'new_first_admitted_units': len(first - previous_first),
                       'cumulative_new_canonical_units': head['new_canonical_units'] + len(receipts),
                       'stats': stats, 'task_cost': bundle['cost'],
                       'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu,
                       'llm_requests_for_write': 0, 'tokens_for_write': 0}
            backend.close()
            backend = None
            save(version / 'commit.json', receipt)
            sha = seal_store(version)
            new_head = {**head, 'version': version.name, 'store': str(version), 'db_sha256': sha,
                        'committed_learn': head['committed_learn'] + [task],
                        'experienced_learn_designs': n + 1, 'stats': stats,
                        'new_canonical_units': receipt['cumulative_new_canonical_units'],
                        'first_admitted_unit_ids': sorted(first), 'updated_at': time.time()}
            save(root / 'head.json', new_head)
            outcome = 'COMMITTED'
            return receipt
        finally:
            if backend is not None:
                backend.close()
            _maintenance(root, {'operation': 'commit', 'task': task, 'version': version.name, 'status': outcome,
                                'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu,
                                'llm_requests': 0, 'tokens': 0})


def _epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def mark_discarded_branch(rows, events):
    starts = [e['ts'] for e in events if e.get('type') == 'task_entry_fallback' and e.get('stage') == 'start']
    result = copy.deepcopy(rows)
    if starts:
        boundary = min(starts)
        for row in result:
            stamp = _epoch(row.get('ts'))
            if stamp is not None and stamp <= boundary:
                row.update(rolled_back=True, rollback_reason='task_entry_fallback_discarded_branch')
            elif stamp is None:
                row['rolled_back'] = None
                row['rollback_reason'] = 'task_entry_fallback_branch_time_unknown'
    return result


def _read_optional(path):
    return load(path) if path.is_file() else {}


def candidate_row(project, event):
    """Convert the LLM's own executed candidate, not its session's other actions."""
    from tehm.adapters.r2g_evidence import parse_config_mk
    turn = event['turn']
    before_dir = project / 'reports/t2a-rollback' / ('turn-%d' % turn)
    before_cfg = parse_config_mk((before_dir / 'constraints/config.mk').read_text()) if (before_dir / 'constraints/config.mk').exists() else {}
    applied = event.get('applied') or {}
    delta = {**applied.get('set', {}), **{k: None for k in applied.get('unset', [])}}
    sit = copy.deepcopy(event.get('situation') or {})
    check = sit.get('check') or 'unknown'
    before_acc = _read_optional(before_dir / 'reports/acceptance.json')
    verdict = event.get('verdict') or {}
    acc = verdict.get('acceptance') or {}
    if check == 'physical' and any(k in (before_acc.get('failed') or []) for k in ('A6_setup', 'A7_hold', 'A8_drv_limits')):
        check = 'timing'
        sit['check'] = check
    row = {'fix_session_id': 'lc-llm-' + project.name, 'iter': turn, 'ts': datetime.fromtimestamp(event['ts'], timezone.utc).isoformat(),
           'check': check, 'violation_class': sit.get('violation_class'), 'llm': True,
           'strategy': 'llm_edit:' + ','.join(sorted(delta)), 'situation': sit,
           'config_before': before_cfg, 'config_delta': delta,
           'cumulative_config': {**before_cfg, **delta}, 'before': None, 'after': None,
           'from_stage': None, 'rolled_back': event.get('rolled_back'), 'verdict': 'stop_not_executed',
           'global_regressions': (event.get('comparison') or {}).get('regressions') or [],
           'full_task_accepted': verdict.get('accepted') is True,
           'provenance': {'ledger_event_seq': event.get('event_seq'), 'repair_turn_id': event.get('repair_turn_id')},
           'capture_kind': 'learning-llm-candidate-v1'}
    if event.get('flow_launched') is not True:
        return row
    row['from_stage'] = applied.get('rerun_from')
    row['verdict'] = 'unknown'
    required = {'A1_flow_complete', 'A2_drc_full_deck', 'A3_lvs', 'A4_route', 'A5_antenna',
                'A6_setup', 'A7_hold', 'A8_drv_limits', 'A9_task_clock', 'A10_rcx', 'A11_provenance'}
    checks = acc.get('checks') or {}
    known = (event.get('flow_rc') == 0 and event.get('status') in ('RETAINED', 'REGRESSION', 'FAILED') and
             set(checks) == required and all(c in ('pass', 'fail') for c in checks.values()) and
             not acc.get('unknown') and (verdict.get('envelope') or {}).get('status') != 'UNVERIFIED' and
             not (event.get('comparison') or {}).get('unknowns'))
    if not known:
        return row
    if check == 'drc':
        row['before'] = _read_optional(before_dir / 'reports/drc.json').get('total_violations')
        row['after'] = (verdict.get('drc') or {}).get('total_violations')
    elif check in ('orfs_stage', 'route'):
        ppa = _read_optional(before_dir / 'reports/ppa.json')
        if ppa.get('orfs_status') in ('partial', 'fail'):
            row['before'] = 1
            row['after'] = 0 if verdict.get('final_layout') else None
    elif check in ('timing', 'timing_check'):
        keys = ('A6_setup', 'A7_hold', 'A8_drv_limits')
        if any(k in (before_acc.get('failed') or []) for k in keys):
            row['before'] = 1
        current = acc.get('checks') or {}
        if all(current.get(k) in ('pass', 'fail') for k in keys):
            row['after'] = int(any(current[k] == 'fail' for k in keys))
    row['verdict'] = ('regression' if row['global_regressions'] else 'cleared'
                      if row['after'] == 0 and row['before'] is not None else 'applied')
    return row


def export_episode(project, task, role):
    """Strict raw capture: malformed/missing records stop the writer, never skip rows."""
    from tehm import neutral_export as N
    from tehm.adapters.r2g_evidence import parse_config_mk
    project = Path(project)
    episode_path = project / 'episode.json'
    episode = load(episode_path)
    if role not in ('LEARN', 'PROBE') or episode.get('status') not in ('ACCEPTED', 'NOT_ACCEPTED', 'UNVERIFIED_OR_SAFETY_STOP'):
        raise ValueError('A terminal registered LEARN/PROBE episode is required')
    if (episode.get('accounting') or {}).get('passed') is not True:
        raise ValueError('Episode accounting did not pass')
    config_path = project / 'constraints/config.mk'
    platform = parse_config_mk(config_path.read_text()).get('PLATFORM')
    if platform not in ('sky130hd', 'sky130hs'):
        raise ValueError('Missing/unrecognized actual episode platform')
    ledger = project / 'reports/resource_ledger.jsonl'
    events = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    fixlog = project / 'reports/fix_log.jsonl'
    rows = [json.loads(l) for l in fixlog.read_text().splitlines() if l.strip()] if fixlog.is_file() else []
    if any(not r.get('fix_session_id') for r in rows):
        raise ValueError('Cannot silently drop a fix row without physical-attempt identity')
    rows = mark_discarded_branch(rows, events)
    candidates = [e for e in events if e.get('type') == 'repair_candidate']
    normalized = [candidate_row(project, e) for e in candidates]
    normalized = mark_discarded_branch(normalized, events)
    source = {'path': str(fixlog), 'design': task, 'platform': platform, 'project': project.name,
              'rows': list(enumerate(rows, 1))}
    llm_source = {'path': str(ledger), 'design': task, 'platform': platform, 'project': project.name,
                  'rows': [(e.get('event_seq', i), r) for i, (e, r) in enumerate(zip(candidates, normalized), 1)]}
    units, dedup = N.build_units([source, llm_source])
    binding_path = project / '.tehm-store/growth-binding.json'
    paths = [episode_path, ledger, config_path, *([fixlog] if fixlog.is_file() else []),
             *([binding_path] if binding_path.is_file() else [])]
    paths += [p for p in (project / 'reports/t2a-rollback').rglob('*') if p.is_file()]
    hashes = {str(p): digest(p) for p in paths}
    return {'schema': 'learning-episode-export-v1', 'task': task, 'role': role, 'platform': platform,
            'task_start_binding': load(binding_path) if binding_path.is_file() else None,
            'episode_status': episode['status'], 'input_hashes': hashes, 'units': units,
            'deduplication': dedup, 'candidate_audit': candidates,
            'network_request_audit': [e for e in events if e.get('type') == 'llm_request'],
            'capture_complete': True, 'raw_fix_rows': len(rows), 'llm_candidate_rows': len(candidates),
            'cost': {'wall_s': episode.get('total_worker_wall_s', episode.get('wall_s')),
                     'tokens': episode.get('token_usage', 'UNKNOWN'), 'resources': episode.get('resources', 'UNKNOWN'),
                     'accounting': episode['accounting'], 'ledger_sha256': digest(ledger)},
            'new_requests': 0, 'new_eda': 0,
            'note': 'Preserved episode replay/capture, not a new physical repair or learning-effect measurement'}


def replay_check(runtime, output, code_package=None):
    """Replay closed real DEV trajectories in scratch stores, with zero EDA/network.

    This tests writer integration, not whether the growing memory repaired those
    old tasks. Their actions were originally chosen with a frozen store.
    """
    import sys
    runtime, output = Path(runtime), Path(output)
    if output.exists():
        raise RuntimeError('Existing replay diagnostic; no implicit repetition')
    reg = load(runtime / 'registration.json')
    verify(reg['files'])
    code = runtime / 'code'
    package_reg = None
    if code_package:
        code_package = Path(code_package)
        package_reg = load(code_package / 'registration.json')
        if (package_reg.get('schema') != 'learning-runtime-supplement-v1' or
                package_reg['parent_registration_sha256'] != digest(runtime / 'registration.json')):
            raise ValueError('Supplement does not bind the replay parent')
        verify(package_reg['files'])
        code = code_package / 'code'
    sys.path[:0] = [str(code / 'memory'), str(code / 'memory/scripts')]
    projects = [runtime / 'episodes' / n for n in ('chacha_core__u72__TM0__r1', 'picorv32__u89__TM0__r1')]
    llm_project = runtime / 'episodes/riscv_core__u19__TM0__r1'
    # Exports bind all original receipt/log bytes; no edits to old experiments.
    exports = [export_episode(p, 'dev-' + p.name, 'LEARN') for p in projects]
    llm_export = export_episode(llm_project, 'dev-llm-audit', 'LEARN')
    output.mkdir(parents=True)
    import shutil
    (output / 'operator').mkdir()
    shutil.copy2(__file__, output / 'operator' / Path(__file__).name)
    save(output / 'registration.json', {'schema': 'learning-growth-replay-check-v1', 'time': time.time(),
         'operator_sha256': digest(__file__), 'runtime_registration_sha256': digest(runtime / 'registration.json'),
         'code_package_registration': str(code_package / 'registration.json') if code_package else None,
         'code_package_registration_sha256': digest(code_package / 'registration.json') if code_package else None,
         'runtime_files': reg['files'], 'source_receipts': [str(p / 'episode.json') for p in projects + [llm_project]],
         'no_new_model_or_eda': True, 'not_learning_effect_data': True,
         'initial_store_sha256': digest(runtime / 'store/tehm.sqlite')})
    started, cpu = time.time(), time.process_time()
    state = output / 'state'
    before = initialize(state, runtime / 'store', [b['task'] for b in exports], ['dev-probe'])
    first_probe = output / 'probe-before'
    bind(state, 'dev-probe', 'PROBE', first_probe)
    first_sha = digest(first_probe / 'tehm.sqlite')
    receipts = []
    for i, bundle in enumerate(exports):
        binding = bind(state, bundle['task'], 'LEARN', output / ('learn-start-%d' % i))
        bundle['task_start_binding'] = binding
        bundle['diagnostic_replay'] = True
        receipts.append(commit(state, bundle['task'], bundle))
    bind(state, 'dev-probe', 'PROBE', output / 'probe-after')
    after = load(state / 'head.json')
    save(output / 'llm-export-audit.json', llm_export)
    # Every request is retained, including discarded/failed candidates. Preserve
    # actual HS platform in this separate capture audit; never import it as HD.
    candidates = llm_export['candidate_audit']
    checks = {'initial_store_nonempty': before['stats']['canonical_units'] > 0,
        'source_m0_unchanged': digest(runtime / 'store/tehm.sqlite') == before['initial_db_sha256'],
        'old_probe_unchanged': digest(first_probe / 'tehm.sqlite') == first_sha,
        'ordered_commits': after['committed_learn'] == [b['task'] for b in exports],
        'grew_canonical_evidence': after['stats']['canonical_units'] > before['stats']['canonical_units'],
        'all_real_llm_candidates_captured': llm_export['llm_candidate_rows'] == len(candidates) and len(candidates) > 0,
        'llm_request_audit_complete': len(llm_export['network_request_audit']) ==
            llm_export['cost']['accounting']['llm_requests'],
        'no_hs_relabeling': llm_export['platform'] == 'sky130hs' and all(u['identity']['platform'] == 'sky130hs' for u in llm_export['units']),
        'runtime_inputs_unchanged': all(digest(k) == v for k, v in reg['files'].items())}
    if package_reg:
        checks['supplement_inputs_unchanged'] = all(digest(k) == v for k, v in package_reg['files'].items())
    for b in exports + [llm_export]:
        verify(b['input_hashes'])
    result = {'schema': 'learning-growth-replay-check-result-v1',
        'status': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks, 'commits': receipts,
        'before_units': before['stats']['canonical_units'], 'after_units': after['stats']['canonical_units'],
        'historical_llm_candidates_audited': len(candidates), 'new_llm_requests': 0, 'new_eda_flows': 0,
        'paper_data': False, 'not_an_online_repair_run': True,
        'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu,
        'remaining': ['Final validated runtime freeze', 'Paid episode/dispatcher integration',
                      'Qualification holds', 'Existing bank allocation and live balance reconciliation']}
    save(output / 'result.json', result)
    print(stable({k: result[k] for k in ('status', 'checks', 'before_units', 'after_units', 'wall_s')}), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    import argparse
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['replay-check'])
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--code-package', type=Path)
    args = p.parse_args()
    sys.exit(replay_check(args.runtime.resolve(), args.output.resolve(),
                          args.code_package.resolve() if args.code_package else None))
