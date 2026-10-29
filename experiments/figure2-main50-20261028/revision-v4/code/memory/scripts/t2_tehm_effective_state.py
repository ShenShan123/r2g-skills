"""Version-bound preconditions, independent of success/admission annotations.

An omitted setting is not an unknown value when the exact ORFS declaration is
bound. Historical omissions need their own hash-bound toolchain provenance;
they never borrow the current tool's default merely because a knob has a name.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


REGISTRY = Path(__file__).with_name('figure2-effective-defaults.json')


def unit_digest(unit):
    return hashlib.sha256(json.dumps(unit, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def load_registry():
    registry = json.loads(REGISTRY.read_text())
    if registry.get('schema') != 'figure2-effective-defaults-v1':
        raise ValueError('Unregistered effective-defaults schema')
    source = Path(registry['variables_file'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != registry['variables_sha256']:
        raise ValueError('Frozen ORFS defaults declaration changed')
    return registry


def current_execution(project, config_path):
    """Only the active, configuration-bound run can supply current values."""
    project=Path(project)
    try:
        pointer=json.loads((project/'backend/.r2g_signoff_run').read_text())
        run=Path(pointer['run_dir']).resolve()
        if not run.is_relative_to((project/'backend').resolve()):return None
        path=run/'effective-config.json';raw=path.read_bytes();receipt=json.loads(raw)
        if (receipt.get('status')!='PASS' or receipt.get('errors') or
            receipt.get('project_config_sha256')!=hashlib.sha256(Path(config_path).read_bytes()).hexdigest()):
            return None
        return {'values':receipt['effective'],'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
    except (OSError,ValueError,KeyError,TypeError):
        return None


def resolve(cfg, knobs, registry, *, historical_unit=None, execution=None):
    """Return value AND authority; UNKNOWN is never converted to a default."""
    result = {}
    for knob in knobs:
        if knob in cfg:
            raw = cfg[knob]
            source = 'explicit_config'
            bound = True
        else:
            source = 'bound_effective_execution'
            bound = True
            if historical_unit is not None:
                proof = registry.get('historical_units', {}).get(historical_unit['unit_id'])
                bound = bool(proof and proof.get('unit_sha256') == unit_digest(historical_unit))
                execution=None
                if bound and proof.get('pre_execution'):
                    ref=proof['pre_execution'];path=Path(ref['path']);data=path.read_bytes()
                    if hashlib.sha256(data).hexdigest()!=ref['sha256']:
                        raise ValueError('Historical effective-state receipt changed')
                    receipt=json.loads(data)
                    if receipt.get('status')=='PASS' and not receipt.get('errors'):
                        execution={'values':receipt.get('effective',{}),**ref}
            effective=(execution or {}).get('values',{}).get(knob) or {}
            raw=effective.get('value') if bound and effective.get('origin') not in (None,'undefined') else None
            if raw is not None and effective.get('origin')=='file':
                try:
                    if float(raw)==registry.get('defaults',{}).get(knob):source='hash_bound_orfs_default'
                except (TypeError,ValueError):pass
        try:
            number = float(raw) if not isinstance(raw, bool) else float('nan')
        except (ValueError, TypeError):
            number = float('nan')
        if not math.isfinite(number):
            result[knob] = {'status':'UNKNOWN', 'value':None,
                'reason':'historical_toolchain_unbound' if not bound else 'effective_value_unresolved'}
        else:
            result[knob] = {'status':'KNOWN', 'value':number, 'source':source}
            if source != 'explicit_config':
                result[knob].update(execution_receipt=(execution or {}).get('path'),
                                    execution_sha256=(execution or {}).get('sha256'))
            if source == 'hash_bound_orfs_default':
                result[knob].update(variables_file=registry['variables_file'],
                                    variables_sha256=registry['variables_sha256'])
    return result
