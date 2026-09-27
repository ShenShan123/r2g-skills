"""Build and cold-verify bounded gen6 v8 RTL TRAIN M−/M+/Mremove.

This is researcher-assisted static TRAIN Memory. It is not an unseen target
experiment, an online update, production promotion, or Mremove attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from contracts import MemoryQuery
from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v8 as train
from tehm.artifact_store import ArtifactStore
from tehm.assets import r5_train_evidence_v8 as evidence
from tehm.assets import r5_train_raw_v8 as raw
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v8 import with_skid_payload_binding_v8
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import build_transition_causal_fragment, consolidate_causal_path
from tehm.causal.replication import evaluate_replicated_effect
from tehm.ids import stable_dumps
from tehm.knowledge import (build_knowledge_from_path, get_knowledge_by_object_id,
                            get_knowledge_status, record_knowledge_authority,
                            register_knowledge, set_knowledge_status,
                            verify_knowledge_authority)
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.rtl.skid_payload_action_v8 import PROFILE, payload_from_source_v8
from tehm.sync import export_bundle, verify_bundle
from tehm.verified_execution import require_verified_transition, scoped_learning_replay

ROOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')
EPOCH_DIR = ROOT / 'epochs'
MEMORY_DIR = ROOT / 'memory'
ASSET_PACKAGE = ROOT / 'training/skid-v8-authority-r1'
KNOWLEDGE_GATE = ROOT / 'training/skid-v8-knowledge-ram-r1.json'
ASSET_SEAL_SHA = '5e3c5ccdd477114bdf86ac1e8e36849063bc4c0780bd7727c4e17e3dcb62d66d'
ASSET_RECEIPT_SHA = '4a3c4ab7c9c833f5c046ef560bb64c0850f00dbe8fdd22da4f57eb5f2abae1cd'
KNOWLEDGE_GATE_SHA = 'dc850edd4bfe21e3c59bcf52e386b2a13038d0f03dde5e78513babf51c676c9e'
SCHEMA = 'tehm-r5-rtl-train-readonly-m0-gen6-v8-spec-v1'
REPORT_SCHEMA = 'tehm-r5-rtl-train-readonly-m0-gen6-v8-report-v1'
DELTA_SCHEMA = 'tehm-r5-rtl-train-m0-gen6-v8-delta-v1'
ACQUISITIONS_SCHEMA = 'tehm-r5-rtl-train-gen6-v8-parent-acquisitions-v1'
CAMPAIGN = train.CAMPAIGN
CASE_ORDER = evidence.CASE_ORDER


def _sha(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _digest(value: object) -> str:
    return _sha(stable_dumps(value).encode())


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _software(*, require_clean: bool = True) -> dict:
    repo = _repo()
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()
    status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1',
                             '--untracked-files=all'], check=True, capture_output=True,
                            text=True, timeout=20).stdout
    if require_clean and status:
        raise ValueError('gen6 M0 requires a clean software epoch')
    paths = sorted((repo / 'memory/tehm').rglob('*.py'))
    paths.append(repo / 'memory/contracts.py')
    return {'git_head': head, 'builder_sha256': _sha(Path(__file__).read_bytes()),
            'files_sha256': {str(path.relative_to(repo)): _sha(path.read_bytes())
                             for path in paths}}


def _software_matches(saved: object, *, require_clean_epoch: bool) -> bool:
    current = _software(require_clean=require_clean_epoch)
    if not isinstance(saved, dict) or set(saved) != {'git_head', 'builder_sha256', 'files_sha256'}:
        return False
    head, files = saved['git_head'], saved['files_sha256']
    if not isinstance(head, str) or re.fullmatch(r'[0-9a-f]{40}', head) is None:
        return False
    inventory = subprocess.run(['git', '-C', str(_repo()), 'ls-tree', '-r',
                                '--name-only', head, '--', 'memory/tehm',
                                'memory/contracts.py'], capture_output=True,
                               text=True, timeout=20)
    if inventory.returncode != 0:
        return False
    committed = {name for name in inventory.stdout.splitlines()
                 if name == 'memory/contracts.py' or
                 (name.startswith('memory/tehm/') and name.endswith('.py'))}
    return bool(isinstance(files, dict) and set(files) == committed and
                saved['builder_sha256'] == current['builder_sha256'] and
                (not require_clean_epoch or head == current['git_head']) and
                files == current['files_sha256'])


def _asset_gate() -> dict:
    root = ASSET_PACKAGE
    if not root.is_dir() or root.is_symlink():
        raise ValueError('v8 Asset gate package missing or linked')
    seal_path, receipt_path = root / 'seal.json', root / 'receipt.json'
    if (_sha(seal_path.read_bytes()) != 'sha256:' + ASSET_SEAL_SHA or
            _sha(receipt_path.read_bytes()) != 'sha256:' + ASSET_RECEIPT_SHA):
        raise ValueError('v8 Asset gate seal/receipt drift')
    seal = json.loads(seal_path.read_bytes())
    if (seal.get('schema') != 'r5-v8-strict-authority-checkpoint-seal-v1' or
            seal.get('recovery_receipt_sha256') != ASSET_RECEIPT_SHA or
            not isinstance(seal.get('files'), dict)):
        raise ValueError('v8 Asset gate seal malformed')
    expected = seal['files']
    observed = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('linked v8 Asset gate evidence')
        if path.is_file() and path.relative_to(root).as_posix() != 'seal.json':
            observed[path.relative_to(root).as_posix()] = _sha(path.read_bytes()).split(':', 1)[1]
    if observed != expected:
        raise ValueError('v8 Asset gate inventory drift')
    receipt = json.loads(receipt_path.read_bytes())
    if (receipt.get('schema') != 'r5-v8-strict-authority-recovery-v1' or
            receipt.get('ram_checks_per_copy') != 30 or
            receipt.get('persistent_memory_changed') is not False or
            receipt.get('model_calls') != 0):
        raise ValueError('v8 Asset gate recovery receipt malformed')
    original = json.loads((root / 'original-r2/stdout.log').read_bytes())
    recovered = json.loads((root / 'recovery/stdout.log').read_bytes())
    if (original != recovered or original.get('valid') is not True or
            original.get('case_count') != 30 or
            original.get('raw_train_digest') != train.RAW_DIGEST or
            original.get('lineage_audit_digest') != train.LINEAGE_DIGEST or
            original.get('asset_authority_receipt_digest') !=
            'sha256:56a4bbd3bc8f79b61d12e3bba961e427def0bf94971491742c7ef6cc8f70d940'):
        raise ValueError('v8 Asset gate cold results drift')
    return {'seal_sha256': 'sha256:' + ASSET_SEAL_SHA,
            'receipt_sha256': 'sha256:' + ASSET_RECEIPT_SHA,
            'strict_authority_receipt_digest': original['asset_authority_receipt_digest']}


def _knowledge_gate() -> dict:
    if not KNOWLEDGE_GATE.is_file() or KNOWLEDGE_GATE.is_symlink():
        raise ValueError('v8 Knowledge gate missing or linked')
    if _sha(KNOWLEDGE_GATE.read_bytes()) != 'sha256:' + KNOWLEDGE_GATE_SHA:
        raise ValueError('v8 Knowledge gate receipt drift')
    gate = json.loads(KNOWLEDGE_GATE.read_bytes())
    software = gate.get('software') or {}
    expected_paths = ('memory/tehm/adapters/research_r5_rtl_scoped_v8.py',
                      'memory/tehm/evaluation/research_r5_rtl_scoped_v8_checks.py',
                      'memory/tehm/verified_execution.py')
    if (gate.get('schema') != 'tehm-r5-v8-canonical-knowledge-ram-checks-v1' or
            gate.get('valid') is not True or gate.get('case_count') != 31 or
            gate.get('knowledge_authority_eligible') is not True or
            gate.get('ram_only_research_validation') is not True or
            gate.get('memory_m_plus_constructed') is not False or
            gate.get('heldout_transfer') is not False or
            gate.get('raw_train_digest') != train.RAW_DIGEST or
            gate.get('lineage_audit_digest') != train.LINEAGE_DIGEST or
            gate.get('source_groups') != sorted(evidence.REPOSITORIES.values()) or
            gate.get('shared_contract_digest') != train._digest(train.MEASUREMENT_CONTRACT) or
            software.get('tree_clean') is not True or
            any(software.get('files_sha256', {}).get(name) !=
                _sha((_repo() / name).read_bytes()) for name in expected_paths)):
        raise ValueError('v8 Knowledge gate role/software drift')
    return {'receipt_sha256': 'sha256:' + KNOWLEDGE_GATE_SHA,
            'strict_authority_receipt_digest': gate['knowledge_authority_receipt_digest']}


def _spec() -> dict:
    return {'schema': SCHEMA, 'role': 'RESEARCHER_ASSISTED_TRAIN_READ_ONLY_M0',
            'campaign_id': CAMPAIGN, 'target_scope': PROFILE,
            'materialized_at': datetime.now(timezone.utc).isoformat(),
            'software': _software(),
            'gate_receipts': {'asset': _asset_gate(), 'knowledge': _knowledge_gate()},
            'model_call_limit': 0, 'online_memory_update': False,
            'production_authority': False, 'heldout_transfer': False}


def prepare(path: Path) -> dict:
    target = path.resolve(strict=False)
    if target.parent != EPOCH_DIR or target.exists() or target.is_symlink():
        raise ValueError('gen6 M0 spec must be a new direct child of epochs')
    spec = _spec()
    _write_json(target, spec)
    return {'prepared': True, 'spec': str(target),
            'spec_sha256': _sha(target.read_bytes()), 'git_head': spec['software']['git_head']}


def _read_spec(path: Path, *, require_clean_epoch: bool = True) -> dict:
    source = path.resolve(strict=True)
    if source.parent != EPOCH_DIR or source.is_symlink():
        raise ValueError('gen6 M0 spec is outside evaluator epoch directory')
    spec = json.loads(source.read_bytes())
    if (spec.get('schema') != SCHEMA or spec.get('campaign_id') != CAMPAIGN or
            spec.get('target_scope') != PROFILE or
            spec.get('role') != 'RESEARCHER_ASSISTED_TRAIN_READ_ONLY_M0' or
            spec.get('gate_receipts') != {'asset': _asset_gate(),
                                          'knowledge': _knowledge_gate()} or
            not _software_matches(spec.get('software'),
                                  require_clean_epoch=require_clean_epoch) or
            spec.get('model_call_limit') != 0 or
            spec.get('online_memory_update') is not False or
            spec.get('production_authority') is not False or
            spec.get('heldout_transfer') is not False or
            not isinstance(spec.get('materialized_at'), str)):
        raise ValueError('gen6 M0 frozen spec or software epoch drift')
    return spec


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tehm_%' ORDER BY name")]
    return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in names}


def _semantic_rows(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {name: sorted(stable_dumps(dict(row)) for row in
                         conn.execute(f'SELECT * FROM "{name}"').fetchall())
            for name in _table_counts(conn)}


def _remove_delta(plus: sqlite3.Connection, minus: sqlite3.Connection,
                  removed: sqlite3.Connection) -> dict[str, int]:
    plus_counts, minus_counts = _table_counts(plus), _table_counts(minus)
    if plus_counts.keys() != minus_counts.keys():
        raise ValueError('gen6 Mremove schema mismatch')
    plus.commit()
    plus.backup(removed)
    closure = {}
    for name in sorted(plus_counts):
        delta = plus_counts[name] - minus_counts[name]
        if delta < 0 or (delta > 0 and minus_counts[name] != 0):
            raise ValueError('gen6 Mremove has mixed shared/delta rows: ' + name)
        if delta:
            removed.execute(f'DELETE FROM "{name}"')
            closure[name] = delta
    if not closure or _semantic_rows(removed) != _semantic_rows(minus):
        raise ValueError('gen6 Mremove failed M− semantic equivalence')
    removed.commit()
    return closure


def _backup(conn: sqlite3.Connection, path: Path) -> None:
    conn.commit()
    output = sqlite3.connect(str(path))
    try:
        conn.backup(output)
        output.commit()
    finally:
        output.close()
    path.chmod(0o444)


def _query(knowledge) -> MemoryQuery:
    measurement = knowledge.intervention['measurement_contract']
    return MemoryQuery(query_plan={
        'mechanism_family': knowledge.mechanism_family,
        'compatibility_profile': knowledge.compatibility_profile,
        'target_scope': PROFILE,
        'transformation_family': 'skid_payload_restore_shadow_v8',
        'measurement_contract_digest': measurement['contract_digest'],
        'design_id': 'axis_register_train_consumption_preflight'})


def _build_plus(conn: sqlite3.Connection, store: ArtifactStore,
                materialized_at: str) -> tuple[dict, dict]:
    checked = {case: train.verify_acquisition(train.acquisition(case, 'treatment'))
               for case in CASE_ORDER}
    acquisitions, ids = {}, {}
    for case in CASE_ORDER:
        ids[case] = {}
        for role in ('control', 'treatment'):
            item = train.acquisition(case, role)
            receipt = capture(conn, store, train.build_record(item),
                              materialized_at=materialized_at,
                              dataset_campaign_id=CAMPAIGN, dataset_split='training',
                              dataset_learner_eligible=True)
            acquisitions[receipt.transition_id] = item
            ids[case][role] = receipt.transition_id
    acq_digest = _digest(acquisitions)
    with scoped_learning_replay(conn, campaign_id=CAMPAIGN, acquisitions=acquisitions,
                                expected_digest=acq_digest):
        for transition_id in acquisitions:
            require_verified_transition(conn, transition_id)
        pairs = [build_intervention_pair(
            conn, ids[case]['control'], ids[case]['treatment'], campaign_id=CAMPAIGN,
            target_scope=PROFILE).to_dict() for case in CASE_ORDER]
        if any(pair['validity_status'] != 'VALID_CONTROLLED_PAIR' or
               pair['evidence_level'] != 'L2_CONTROLLED_INTERVENTION' for pair in pairs):
            raise ValueError('gen6 M+ controlled TRAIN pair rejected')
        fragments = [build_transition_causal_fragment(conn, tid, campaign_id=CAMPAIGN)
                     for tid in sorted(acquisitions)]
        path = consolidate_causal_path(conn, fragments, campaign_id=CAMPAIGN, status='shadow')
        replication = evaluate_replicated_effect(conn, path.path_id, campaign_id=CAMPAIGN)
        if (not replication.eligible or replication.evidence_level != 'L3_REPLICATED_EFFECT' or
                set(replication.unique_lineages) != set(evidence.REPOSITORIES.values())):
            raise ValueError('gen6 M+ bounded TRAIN replication rejected')
        knowledge = build_knowledge_from_path(conn, path.path_id)
        register_knowledge(conn, knowledge, target_scope=PROFILE)
        k_authority = record_knowledge_authority(conn, knowledge, target_scope=PROFILE)
        if not k_authority.eligible or not verify_knowledge_authority(conn, k_authority)['eligible']:
            raise ValueError('gen6 M+ Knowledge authority rejected')
        set_knowledge_status(conn, knowledge_id=knowledge.knowledge_id,
                             version=knowledge.version, target_scope=PROFILE,
                             status='validated', authority_receipt=k_authority,
                             provenance={'purpose': 'revision5_gen6_read_only_research_m0'})
        validated = get_knowledge_by_object_id(conn, knowledge.object_id, target_scope=PROFILE)
        first = checked[CASE_ORDER[0]]
        source = train._source(first)
        proposal = build_rtl_asset_proposal(
            {}, name='r5-skid-v8-train-validation-draft',
            transformation_family='skid_payload_restore_shadow_v8',
            action_payload_template=payload_from_source_v8(source, first['public_context']),
            compatibility_profile=PROFILE,
            verifier_obligations=('TRAIN target', 'TRAIN preservation'),
            creator='researcher_assisted_reused_dev_train_generation_6_v8',
            mechanism_knowledge_ids=[knowledge.object_id])
        proposal = with_skid_payload_binding_v8(proposal, source, first['public_context'])
        registration = register_asset_proposal(conn, proposal, target_scope=PROFILE)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError('gen6 M+ Asset registration missing')
        expected = evidence.materialize(asset)
        validations = [{'receipt': value, 'split': 'training', 'source_id': case,
                        'lineage_id': evidence.REPOSITORIES[case]}
                       for case, value in zip(CASE_ORDER, expected['validations'])]
        bindings = [{'asset': value, 'split': 'training', 'source_id': case,
                     'lineage_id': evidence.REPOSITORIES[case]}
                    for case, value in zip(CASE_ORDER, expected['bindings'])]
        if not evidence.verify_train_row_metadata(validations, bindings):
            raise ValueError('gen6 M+ TRAIN Asset metadata mismatch')
        a_authority = record_asset_authority(
            conn, asset_id=registration.asset_id, target_scope=PROFILE,
            validation_receipts=validations, bindings=bindings,
            rollback_receipt={'receipt': expected['rollback'], 'split': 'ab',
                              'source_id': 'v8-train-source-rollback'},
            min_lineages=3)
        if not a_authority.eligible or not verify_asset_authority(conn, a_authority)['eligible']:
            raise ValueError('gen6 M+ Asset authority rejected')
        for status in ('shadow', 'candidate'):
            set_asset_status(conn, asset_id=registration.asset_id,
                             target_scope=PROFILE, status=status)
        query = _query(knowledge)
        routing = route_memory(conn, query, no_memory_budget=1, memory_budget=1,
                               persist_state=False, commit=False)
        selection = select_knowledge_grounded_assets(
            conn, query, routing=routing, rtl_source_text=source,
            design_id=CASE_ORDER[0], rtl_public_context=first['public_context'])
        if routing.decision != 'CONSIDER' or selection.receipt.decision != 'SELECT':
            raise ValueError('gen6 M+ TRAIN consumption did not select')
        report = {'schema': REPORT_SCHEMA, 'role': 'TRAIN_READ_ONLY_M_PLUS_NOT_TRANSFER',
                  'campaign_id': CAMPAIGN, 'target_scope': PROFILE,
                  'transition_ids': ids, 'acquisition_digest': acq_digest,
                  'controlled_pairs': pairs, 'causal_path_id': path.path_id,
                  'replication': replication.to_dict(),
                  'knowledge_object_id': knowledge.object_id,
                  'knowledge': validated.to_dict(),
                  'knowledge_authority': k_authority.to_dict(),
                  'asset_id': registration.asset_id,
                  'asset_status': get_asset_status(conn, asset_id=registration.asset_id,
                                                   target_scope=PROFILE)['status'],
                  'asset_authority': a_authority.to_dict(),
                  'train_consumption_preflight': {
                      'query_plan': dict(query.query_plan), 'routing': routing.to_dict(),
                      'selection': selection.receipt.to_dict()},
                  'model_calls': 0, 'model_tokens': 0, 'online_memory_update': False,
                  'production_authority': False, 'heldout_transfer': False,
                  'source_group_claim': 'bounded_recorded_file_origin_groups_only'}
    report['table_counts'] = _table_counts(conn)
    return report, acquisitions


def build(*, spec: Path, output: Path) -> dict:
    spec_path = spec.resolve(strict=True)
    epoch = _read_spec(spec_path)
    target = output.resolve(strict=False)
    if target.parent != MEMORY_DIR or target.exists() or target.is_symlink():
        raise ValueError('gen6 M0 output must be a new direct child of pilot memory')
    minus, plus, removed = (sqlite3.connect(':memory:') for _ in range(3))
    minus.row_factory = plus.row_factory = removed.row_factory = sqlite3.Row
    original_now = db.now_local
    try:
        db.now_local = lambda: epoch['materialized_at']
        db.ensure_schema(minus)
        db.ensure_schema(plus)
        with tempfile.TemporaryDirectory(prefix=target.name + '.tmp.',
                                         dir=target.parent) as scratch_name:
            scratch = Path(scratch_name)
            artifacts = {view: scratch / (view + '-artifacts')
                         for view in ('m-minus', 'm-plus', 'mremove')}
            for path in artifacts.values():
                path.mkdir()
            report, acquisitions = _build_plus(plus, ArtifactStore(artifacts['m-plus']),
                                                epoch['materialized_at'])
            query = MemoryQuery(query_plan=report['train_consumption_preflight']['query_plan'])
            minus_route = route_memory(minus, query, no_memory_budget=1, memory_budget=1,
                                       persist_state=False, commit=False)
            minus_select = select_knowledge_grounded_assets(minus, query, routing=minus_route)
            if minus_select.receipt.decision == 'SELECT':
                raise ValueError('empty gen6 M− unexpectedly selected')
            minus_counts = _table_counts(minus)
            delta = {name: report['table_counts'][name] - minus_counts[name]
                     for name in sorted(minus_counts)}
            if any(value < 0 for value in delta.values()) or not any(delta.values()):
                raise ValueError('gen6 M0 delta empty or invalid')
            closure = _remove_delta(plus, minus, removed)
            removed_route = route_memory(removed, query, no_memory_budget=1,
                                         memory_budget=1, persist_state=False, commit=False)
            removed_select = select_knowledge_grounded_assets(
                removed, query, routing=removed_route)
            if (removed_route.to_dict() != minus_route.to_dict() or
                    removed_select.receipt.to_dict() != minus_select.receipt.to_dict()):
                raise ValueError('gen6 Mremove routing/selection not M− equivalent')
            report.update({'m_minus_train_consumption_preflight': {
                'routing': minus_route.to_dict(), 'selection': minus_select.receipt.to_dict()},
                'mremove_train_consumption_preflight': {
                    'routing': removed_route.to_dict(), 'selection': removed_select.receipt.to_dict()},
                'mremove_removed_dependency_tables': closure,
                'mremove_table_counts': _table_counts(removed),
                'm_minus_table_counts': minus_counts, 'delta_table_counts': delta,
                'spec_sha256': _sha(spec_path.read_bytes()), 'spec_filename': spec_path.name,
                'software': epoch['software']})
            research = scratch / 'research'
            research.mkdir()
            acquisitions_doc = {'schema': ACQUISITIONS_SCHEMA, 'campaign_id': CAMPAIGN,
                                'acquisitions': acquisitions, 'digest': _digest(acquisitions)}
            delta_doc = {'schema': DELTA_SCHEMA, 'source_view': 'M_MINUS',
                         'target_view': 'M_PLUS', 'removal_view': 'MREMOVE',
                         'source_knowledge_object_ids': [],
                         'added_knowledge_object_ids': [report['knowledge_object_id']],
                         'source_asset_ids': [], 'added_asset_ids': [report['asset_id']],
                         'source_transition_ids': [],
                         'added_transition_ids': sorted(acquisitions),
                         'added_causal_path_ids': [report['causal_path_id']],
                         'table_row_deltas': delta,
                         'removed_dependency_tables': closure,
                         'mremove_equivalent_to_m_minus': True,
                         'TRAIN_ONLY': True, 'online_evolution': False}
            _write_json(research / 'parent-acquisitions.json', acquisitions_doc)
            _write_json(research / 'delta-manifest.json', delta_doc)
            report['parent_acquisitions_sha256'] = _sha((research / 'parent-acquisitions.json').read_bytes())
            report['delta_manifest_sha256'] = _sha((research / 'delta-manifest.json').read_bytes())
            report['report_digest'] = _digest(report)
            _write_json(research / 'm0-build-report.json', report)
            db_paths = {view: scratch / (view + '.sqlite')
                        for view in ('m-minus', 'm-plus', 'mremove')}
            for view, conn in (('m-minus', minus), ('m-plus', plus), ('mremove', removed)):
                _backup(conn, db_paths[view])
            for view in ('m-minus', 'm-plus', 'mremove'):
                sidecars = [(spec_path, 'research/source-epoch.json')]
                if view == 'm-plus':
                    sidecars.append((research / 'parent-acquisitions.json',
                                     'research/parent-acquisitions.json'))
                if view == 'mremove':
                    sidecars.append((research / 'delta-manifest.json',
                                     'research/delta-manifest.json'))
                export_bundle(output=scratch / 'bundles' / view, db_path=db_paths[view],
                              artifact_root=artifacts[view], evidence_files=sidecars,
                              metadata={'purpose': 'Revision5 gen6 bounded read-only RTL TRAIN M0',
                                        'view': view.upper().replace('-', '_'),
                                        'campaign_id': CAMPAIGN, 'target_scope': PROFILE,
                                        'software_git_head': epoch['software']['git_head'],
                                        'production_authority': False,
                                        'online_memory_update': False,
                                        'scoped_replay_required': view == 'm-plus'})
                checked = verify_bundle(scratch / 'bundles' / view)
                if not checked['ok']:
                    raise ValueError('gen6 M0 bundle invalid: ' + checked['detail'])
            os.replace(scratch, target)
        return {'valid': True, 'output': str(target),
                'report_digest': report['report_digest'],
                'knowledge_object_id': report['knowledge_object_id'],
                'asset_id': report['asset_id'], 'heldout_transfer': False,
                'mremove_constructed': True}
    finally:
        db.now_local = original_now
        minus.close()
        plus.close()
        removed.close()


def verify(output: Path) -> dict:
    root = output.resolve(strict=True)
    if root.parent != MEMORY_DIR or root.is_symlink():
        raise ValueError('gen6 M0 output outside pilot memory')
    research = root / 'research'
    report = json.loads((research / 'm0-build-report.json').read_bytes())
    spec_name = report.get('spec_filename')
    if (not isinstance(spec_name, str) or Path(spec_name).name != spec_name or
            not spec_name.endswith('.json')):
        raise ValueError('gen6 M0 spec filename malformed')
    spec_path = EPOCH_DIR / spec_name
    epoch = _read_spec(spec_path, require_clean_epoch=False)
    if (report.get('schema') != REPORT_SCHEMA or
            report.get('spec_sha256') != _sha(spec_path.read_bytes()) or
            report.get('software') != epoch['software'] or
            report.get('report_digest') != _digest({k: v for k, v in report.items()
                                                    if k != 'report_digest'})):
        raise ValueError('gen6 M0 report/epoch drift')
    acq_path, delta_path = research / 'parent-acquisitions.json', research / 'delta-manifest.json'
    if (report.get('parent_acquisitions_sha256') != _sha(acq_path.read_bytes()) or
            report.get('delta_manifest_sha256') != _sha(delta_path.read_bytes())):
        raise ValueError('gen6 M0 sidecar SHA drift')
    acquisitions_doc = json.loads(acq_path.read_bytes())
    acquisitions = acquisitions_doc.get('acquisitions')
    delta_doc = json.loads(delta_path.read_bytes())
    if (acquisitions_doc.get('schema') != ACQUISITIONS_SCHEMA or
            acquisitions_doc.get('campaign_id') != CAMPAIGN or
            not isinstance(acquisitions, dict) or len(acquisitions) != 6 or
            acquisitions_doc.get('digest') != _digest(acquisitions) or
            delta_doc.get('schema') != DELTA_SCHEMA or
            delta_doc.get('TRAIN_ONLY') is not True or
            delta_doc.get('online_evolution') is not False):
        raise ValueError('gen6 M0 sidecar role/digest drift')
    manifests = {}
    for view in ('m-minus', 'm-plus', 'mremove'):
        checked = verify_bundle(root / 'bundles' / view)
        if not checked['ok']:
            raise ValueError('gen6 M0 bundle invalid: ' + checked['detail'])
        manifests[view] = checked['manifest']

    def reload(view: str) -> sqlite3.Connection:
        source = sqlite3.connect('file:' + str(root / 'bundles' / view /
                                               'closed_loop/tehm.sqlite') + '?mode=ro', uri=True)
        ram = sqlite3.connect(':memory:')
        ram.row_factory = sqlite3.Row
        try:
            source.backup(ram)
        finally:
            source.close()
        return ram

    minus, plus, removed = (reload(view) for view in ('m-minus', 'm-plus', 'mremove'))
    recomputed = sqlite3.connect(':memory:')
    recomputed.row_factory = sqlite3.Row
    try:
        if (_table_counts(minus) != report['m_minus_table_counts'] or
                _table_counts(plus) != report['table_counts'] or
                _table_counts(removed) != report['mremove_table_counts']):
            raise ValueError('gen6 M0 cold-loaded table counts drift')
        delta = {name: report['table_counts'][name] - report['m_minus_table_counts'][name]
                 for name in sorted(report['m_minus_table_counts'])}
        expected_delta = {'schema': DELTA_SCHEMA, 'source_view': 'M_MINUS',
                          'target_view': 'M_PLUS', 'removal_view': 'MREMOVE',
                          'source_knowledge_object_ids': [],
                          'added_knowledge_object_ids': [report['knowledge_object_id']],
                          'source_asset_ids': [], 'added_asset_ids': [report['asset_id']],
                          'source_transition_ids': [],
                          'added_transition_ids': sorted(acquisitions),
                          'added_causal_path_ids': [report['causal_path_id']],
                          'table_row_deltas': delta,
                          'removed_dependency_tables': report['mremove_removed_dependency_tables'],
                          'mremove_equivalent_to_m_minus': True,
                          'TRAIN_ONLY': True, 'online_evolution': False}
        if delta_doc != expected_delta or delta != report['delta_table_counts']:
            raise ValueError('gen6 M0 cold-loaded dependency delta drift')
        closure = _remove_delta(plus, minus, recomputed)
        if (closure != report['mremove_removed_dependency_tables'] or
                _semantic_rows(removed) != _semantic_rows(minus) or
                _semantic_rows(removed) != _semantic_rows(recomputed)):
            raise ValueError('gen6 Mremove cold-loaded rebuild not M− equivalent')
        query = MemoryQuery(query_plan=report['train_consumption_preflight']['query_plan'])
        minus_route = route_memory(minus, query, no_memory_budget=1, memory_budget=1,
                                   persist_state=False, commit=False)
        minus_select = select_knowledge_grounded_assets(minus, query, routing=minus_route)
        removed_route = route_memory(removed, query, no_memory_budget=1,
                                     memory_budget=1, persist_state=False, commit=False)
        removed_select = select_knowledge_grounded_assets(
            removed, query, routing=removed_route)
        if (minus_select.receipt.decision == 'SELECT' or
                removed_route.to_dict() != minus_route.to_dict() or
                removed_select.receipt.to_dict() != minus_select.receipt.to_dict() or
                minus_route.to_dict() != report['m_minus_train_consumption_preflight']['routing'] or
                minus_select.receipt.to_dict() != report['m_minus_train_consumption_preflight']['selection'] or
                removed_route.to_dict() != report['mremove_train_consumption_preflight']['routing'] or
                removed_select.receipt.to_dict() != report['mremove_train_consumption_preflight']['selection']):
            raise ValueError('gen6 M−/Mremove cold routing or selection drift')
        with scoped_learning_replay(plus, campaign_id=CAMPAIGN,
                                    acquisitions=acquisitions,
                                    expected_digest=acquisitions_doc['digest']):
            for transition_id in acquisitions:
                require_verified_transition(plus, transition_id)
            knowledge = get_knowledge_by_object_id(
                plus, report['knowledge_object_id'], target_scope=PROFILE)
            status = get_knowledge_status(plus, knowledge_id=knowledge.knowledge_id,
                                          version=knowledge.version, target_scope=PROFILE)
            rebuilt = build_knowledge_from_path(plus, report['causal_path_id'])
            if (knowledge.status != 'validated' or status['status_version'] != 2 or
                    status['provenance'] != {'purpose': 'revision5_gen6_read_only_research_m0'} or
                    report['knowledge_authority'].get('status_version') != 1 or
                    rebuilt.status != 'candidate' or
                    rebuilt.version != knowledge.version + 1 or
                    replace(rebuilt, version=knowledge.version).content_digest !=
                    knowledge.content_digest):
                raise ValueError('gen6 M+ Knowledge lifecycle drift')
            plus.execute('SAVEPOINT gen6_knowledge_authority_replay')
            try:
                plus.execute("UPDATE tehm_mechanism_knowledge_status SET status='candidate', "
                             "status_version=1 WHERE knowledge_id=? AND version=? AND target_scope=?",
                             (knowledge.knowledge_id, knowledge.version, PROFILE))
                k_check = verify_knowledge_authority(plus, report['knowledge_authority'])
            finally:
                plus.execute('ROLLBACK TO SAVEPOINT gen6_knowledge_authority_replay')
                plus.execute('RELEASE SAVEPOINT gen6_knowledge_authority_replay')
            if not k_check['eligible']:
                raise ValueError('gen6 M+ historical Knowledge authority rejected: '
                                 + str(k_check['reasons']))
            if not verify_asset_authority(plus, report['asset_authority'])['eligible']:
                raise ValueError('gen6 M+ Asset authority rejected')
            asset_status = get_asset_status(plus, asset_id=report['asset_id'],
                                            target_scope=PROFILE)
            if asset_status['status'] != 'candidate':
                raise ValueError('gen6 M+ Asset status drift')
            first = train.verify_acquisition(train.acquisition(CASE_ORDER[0], 'treatment'))
            source = train._source(first)
            plus_route = route_memory(plus, query, no_memory_budget=1, memory_budget=1,
                                      persist_state=False, commit=False)
            plus_select = select_knowledge_grounded_assets(
                plus, query, routing=plus_route, rtl_source_text=source,
                design_id=CASE_ORDER[0], rtl_public_context=first['public_context'])
            if (plus_route.decision != 'CONSIDER' or plus_select.receipt.decision != 'SELECT' or
                    plus_route.to_dict() != report['train_consumption_preflight']['routing'] or
                    plus_select.receipt.to_dict() != report['train_consumption_preflight']['selection']):
                raise ValueError('gen6 M+ cold routing or selection drift')
        return {'valid': True, 'output': str(root),
                'report_digest': report['report_digest'],
                'bundle_digests': {view: manifests[view]['bundle_digest']
                                   for view in ('m-minus', 'm-plus', 'mremove')},
                'knowledge_object_id': report['knowledge_object_id'],
                'asset_id': report['asset_id'],
                'm_minus_route': minus_route.decision,
                'm_plus_route': plus_route.decision,
                'mremove_route': removed_route.decision,
                'm_minus_selection': minus_select.receipt.decision,
                'm_plus_selection': plus_select.receipt.decision,
                'mremove_selection': removed_select.receipt.decision,
                'mremove_constructed': True, 'heldout_transfer': False}
    finally:
        minus.close()
        plus.close()
        removed.close()
        recomputed.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare').add_argument('--spec', type=Path, required=True)
    build_parser = sub.add_parser('build')
    build_parser.add_argument('--spec', type=Path, required=True)
    build_parser.add_argument('--output', type=Path, required=True)
    sub.add_parser('verify').add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.spec)
    elif args.command == 'build':
        result = build(spec=args.spec, output=args.output)
    else:
        result = verify(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
