"""Answer-free I2C NACK v3 action stage for M-/M+/Mremove/transform-only.

Contract: memory/evaluation/research_r5_i2c_nack_v3_train_generation_contract_20260929.md (2f),
reusing research_r5_i2c_nack_v2_transform_only_contract_20260928.md.
Runs only inside bwrap: sees one source, its public context, a sanitized handoff and
the frozen software at /app. It never sees oracles, clean counterparts or Memory.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/app')

SCHEMA = 'r5-i2c-v3-source-only-handoff-v1'
VIEWS = ('m-minus', 'm-plus', 'mremove', 'transform-only')
HANDOFF_KEYS = {'schema', 'view', 'source_sha256', 'context_sha256', 'route', 'selection',
                'selected_asset_ids', 'action_payload'}
FORBIDDEN = ('/data1', '/snapshot', '/oracle', '/train', '/.git', '/inputs/.git', '/memory')


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def fail(reason):
    raise SystemExit('consumer rejected: ' + reason)


def main():
    import tehm
    if not Path(tehm.__file__).resolve().is_relative_to(Path('/app')):
        fail('tehm not from frozen /app')
    if any(Path(p).exists() for p in FORBIDDEN):
        fail('evaluator filesystem visible')
    inputs = sorted(str(p.relative_to('/inputs')) for p in Path('/inputs').rglob('*') if p.is_file())
    if inputs != ['public_context.json', 'source.txt']:
        fail('unexpected /inputs files: ' + str(inputs))
    from tehm.evaluation import research_r5_i2c_nack_binding_dev_v3 as binder
    from tehm.rtl import i2c_nack_action_v3 as action
    from tehm.rtl.rtl_actions import apply_rtl_action

    source_bytes = Path('/inputs/source.txt').read_bytes()
    context_bytes = Path('/inputs/public_context.json').read_bytes()
    context = json.loads(context_bytes)
    raw = Path('/handoff.json').read_bytes()
    handoff = json.loads(raw)
    if not isinstance(handoff, dict) or set(handoff) != HANDOFF_KEYS or handoff['schema'] != SCHEMA:
        fail('handoff shape')
    if handoff['view'] not in VIEWS:
        fail('unknown view')
    if handoff['source_sha256'] != sha(source_bytes) or handoff['context_sha256'] != sha(context_bytes):
        fail('source/context identity')
    source = source_bytes.decode('utf-8')
    payload, binding = handoff['action_payload'], None
    if handoff['view'] == 'transform-only':
        if (handoff['route'] != 'BYPASS_MEMORY' or handoff['selection'] != 'DIRECT_PRIMITIVE' or
                handoff['selected_asset_ids'] != [] or payload is not None):
            fail('transform-only handoff carries Memory decision or payload')
        try:
            result = binder.bind(action.ASSET, action._binder_input(source, context), context)
            binding = {'status': result.get('status'), 'reason': result.get('reason')}
        except ValueError as exc:
            binding = {'status': 'INPUT_REJECTED', 'reason': str(exc)}
        if binding['status'] == 'BOUND':
            payload = action.payload_from_source_i2c_v3(source, context)
    elif handoff['selection'] == 'SELECT':
        if len(handoff['selected_asset_ids']) != 1 or not isinstance(payload, dict):
            fail('SELECT without exactly one Asset payload')
        if set(payload) != action.PAYLOAD_KEYS:
            fail('payload fields')
    elif payload is not None or handoff['selected_asset_ids']:
        fail('non-SELECT handoff carries payload or Asset')
    edit = None
    candidate = source
    if payload is not None:
        try:
            rederived = action.payload_from_source_i2c_v3(source, context)
        except ValueError as exc:
            fail('payload not re-derivable from own source: ' + str(exc))
        if payload != rederived:
            fail('stale or tampered payload')
        candidate, edit = apply_rtl_action(source, payload)
    output = candidate.encode('utf-8')
    Path('/out/candidate.txt').write_bytes(output)
    receipt = {'schema': 'r5-i2c-v3-source-only-consumer-v1', 'view': handoff['view'],
               'route': handoff['route'], 'selection': handoff['selection'],
               'action': 'APPLIED' if edit is not None else 'NO_ACTION',
               'binding': binding, 'edit': edit,
               'source_sha256': sha(source_bytes), 'candidate_sha256': sha(output),
               'handoff_sha256': sha(raw), 'context_sha256': sha(context_bytes),
               'source_changed': output != source_bytes,
               'network_namespace_inode': os.stat('/proc/self/ns/net').st_ino,
               'evaluator_filesystem_visible': False, 'model_calls': 0}
    Path('/out/consumer.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
