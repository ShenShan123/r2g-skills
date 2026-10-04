"""Step-1 acceptance checks: I2C NACK v3 core action/Asset integration (contract 5c91a02).

No simulator, no persistent Memory; the RAM database is discarded.
"""
from copy import deepcopy
import json
import sqlite3
import subprocess
from unittest.mock import patch

from tehm import db
from tehm.assets import r5_train_evidence_i2c_v2 as evidence_v2
from tehm.assets import r5_train_raw_i2c_v2 as raw_v2
from tehm.assets.i2c_binding_v2 import is_i2c_v2_asset
from tehm.assets.i2c_binding_v3 import (bind_i2c_asset_to_source_v3, is_i2c_v3_asset,
                                        with_i2c_nack_binding_v3)
from tehm.assets.lifecycle import (ASSET_PROMOTION_GATES, evaluate_asset_authority,
                                   evaluate_asset_promotion_gates)
from tehm.assets.registry import get_asset, set_asset_status
from tehm.assets.skid_binding_v8 import is_skid_v8_asset
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.evaluation import research_r5_i2c_nack_binding_dev_v3 as binder
from tehm.rtl import i2c_nack_action_v2 as action_v2
from tehm.rtl import i2c_nack_action_v3 as action
from tehm.rtl.rtl_actions import apply_rtl_action

CLONE = '/data1/zhangdy/RTL/RTL_testbench/chance189/I2C_Master'
COMMIT = '84cdaab5cfd6e00d594e4273f7b978ca1a0a08a4'
CHANCE_CONTEXT = {'interface': binder.NEW_INTERFACE, 'status_output': 'nack', 'defined_macros': []}
CLEAN_FILES = {'alex': 'original/oracles/alex/candidate/stage/rtl/i2c_master.v',
               'zip': 'original/oracles/zip/candidate/stage/rtl/wbi2cmaster.v',
               'freecores': 'original/oracles/freecores/candidate/stage/rtl/verilog/i2c_master_top.v'}


def sources():
    """case -> (fault transport text, clean transport text, public context, v2 TRAIN candidate hashes|None)."""
    out = {}
    for case in evidence_v2.CASE_ORDER:
        fault = evidence_v2.train_source(case)
        top = (raw_v2.TRAIN / CLEAN_FILES[case]).read_text()
        clean = (action_v2.encode_closure({**action_v2.decode_closure(fault), 'top': top})
                 if case == 'freecores' else top)
        out[case] = (fault, clean, raw_v2.SOURCES[case]['public_context'], raw_v2.SOURCES[case]['candidate_sha256'])
    clean = subprocess.run(['git', '-C', CLONE, 'show', COMMIT + ':i2c_master.v'], check=True,
                           capture_output=True).stdout.decode()
    out['chance189'] = (clean.replace("nack <= 1'b1;", "nack <= 1'b0;"), clean, CHANCE_CONTEXT, None)
    return out


def _hashes(case, text):
    if case == 'freecores':
        return {r: 'sha256:' + evidence_v2.hashlib.sha256(t.encode()).hexdigest()
                for r, t in action_v2.decode_closure(text).items()}
    return 'sha256:' + evidence_v2.hashlib.sha256(text.encode()).hexdigest()


def _rejects(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def check():
    with patch('tehm.db.now_local', return_value='2026-09-29T00:00:00+00:00'):
        return _check()


def _check():
    cases, out = sources(), {}
    for case, (fault, clean, ctx, v2_hashes) in cases.items():
        payload = action.payload_from_source_i2c_v3(fault, ctx)
        candidate, receipt = apply_rtl_action(fault, payload)
        closure = action._binder_input(fault, ctx)
        dev_candidate, _ = binder.apply(action.ASSET, closure, ctx, binder.bind(action.ASSET, closure, ctx))
        dev_text = action_v2.encode_closure(dev_candidate) if isinstance(dev_candidate, dict) else dev_candidate
        out[f'apply_equals_v3_dev_{case}'] = candidate == dev_text and receipt['rewritten'] == 1
        out[f'candidate_equals_clean_{case}'] = candidate == clean
        if v2_hashes is not None:
            out[f'candidate_equals_v2_train_{case}'] = _hashes(case, candidate) == v2_hashes
        out[f'clean_has_no_payload_{case}'] = _rejects(action.payload_from_source_i2c_v3, clean, ctx)
    fault, _, ctx, _ = cases['alex']
    good = action.payload_from_source_i2c_v3(fault, ctx)
    out['stale_source_sha_rejected'] = _rejects(action.apply_i2c_nack_action_v3, fault, {**good, 'source_sha256': 'sha256:' + '0' * 64})
    out['tampered_action_digest_rejected'] = _rejects(action.apply_i2c_nack_action_v3, fault, {**good, 'action_digest': 'sha256:' + '1' * 64})
    out['wrong_payload_keys_rejected'] = _rejects(action.apply_i2c_nack_action_v3, fault, {**good, 'extra': 1})
    fc_fault, _, fc_ctx, _ = cases['freecores']
    out['closure_to_single_file_interface_rejected'] = _rejects(action.payload_from_source_i2c_v3, fc_fault, ctx)
    out['single_file_to_closure_interface_rejected'] = _rejects(action.payload_from_source_i2c_v3, fault, fc_ctx)

    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        proposal = build_rtl_asset_proposal({}, name='r5-i2c-v3-core-check-asset',
            transformation_family='i2c_nack_status_latch_v3',
            action_payload_template=good, compatibility_profile=action.PROFILE,
            verifier_obligations=('TRAIN target', 'TRAIN preservation'), creator='core_integration_check')
        forged = deepcopy(proposal)
        forged.definition['action']['payload'] = {**good, 'source_sha256': 'sha256:' + '2' * 64}
        out['bridge_rejects_non_source_derived_proposal'] = _rejects(with_i2c_nack_binding_v3, forged, fault, ctx)
        registered = register_asset_proposal(conn, with_i2c_nack_binding_v3(proposal, fault, ctx))
        asset = get_asset(conn, registered.asset_id)
        for case, (src, _, c, _) in cases.items():
            bound = bind_i2c_asset_to_source_v3(asset, src, design_id=case + '_core_check', public_context=c)
            out[f'bridge_rebinds_{case}'] = (bound['definition']['action']['payload'] ==
                                             action.payload_from_source_i2c_v3(src, c))
        out['is_v3_true_for_v3'] = is_i2c_v3_asset(asset)
        out['is_v2_false_for_v3'] = not is_i2c_v2_asset(asset)
        out['is_v8_false_for_v3'] = not is_skid_v8_asset(asset)
        v2_like = deepcopy(asset); v2_like['compatibility'] = {'compatibility_profile': 'rtl.i2c.nack_status_latch.v2.dev'}
        v2_like['definition'] = {'action': {'domain': 'rtl.I2C_NACK_STATUS_LATCH_V2'}}
        out['is_v3_false_for_v2'] = not is_i2c_v3_asset(v2_like)
        all_true = {k: True for k in ASSET_PROMOTION_GATES}
        out['boolean_gates_not_eligible'] = not evaluate_asset_promotion_gates(asset, all_true, target_scope=action.PROFILE).eligible
        out['strict_path_pending_until_step2'] = not evaluate_asset_authority(
            asset, validation_receipts=[], bindings=[], rollback_receipt={}, target_scope=action.PROFILE).eligible
        for status in ('shadow', 'candidate'):
            set_asset_status(conn, asset_id=registered.asset_id, target_scope=action.PROFILE, status=status)
        out['nonstrict_promotion_raises'] = _rejects(lambda: set_asset_status(
            conn, asset_id=registered.asset_id, target_scope=action.PROFILE, status='promoted', gates=all_true))
    finally:
        conn.close()
    return {'schema': 'r5-i2c-nack-v3-core-checks-v1', 'contract_commit': '5c91a02',
            'valid': all(out.values()), 'check_count': len(out),
            'failed': sorted(k for k, v in out.items() if not v), 'checks': out,
            'memory_authority_granted': False, 'model_calls': 0, 'simulator_runs': 0}


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result['valid'] else 1)
