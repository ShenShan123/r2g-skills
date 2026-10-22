#!/usr/bin/env python3
"""Re-evaluate a finished GRT component-trial campaign with the failure-patterns #61 readers.

The campaign's executor ran from its frozen code snapshot, whose area reader and ann-v2 (dev.6)
trusted a logs/6_report.json that an early-aborting rerun had carried over from the previous
completed flow. This script leaves the campaign's own records untouched and writes, next to them:

  reevaluation.json        per trial: the original envelope/credit and the corrected ones
  candidate-history-dev7/  ann-v2-dev.7 labels of every trial row + the independent verifier

Corrected envelope: the original configuration and execution-binding verdict (unaffected: they
read the config and the receipt, not the finish report) combined with a fresh area check whose
areas count only when the trial run's own stage log shows finish passing.

  python3 t2_component_trials_reeval.py --root <campaign root>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'memory'))
sys.path.insert(0, str(REPO / 'memory' / 'scripts'))
import t2a_envelope as E  # noqa: E402


# Knobs that ORFS hands to the global router only through scripts/floorplan.tcl's default branch;
# a platform FASTROUTE_TCL replaces that branch (sky130hd hard-codes a 0.2 adjustment).
ROUTER_KNOBS = ("ROUTING_LAYER_ADJUSTMENT",)
_SOURCED = re.compile(r"^\s*source\s+(\S*fastroute[^\s]*\.tcl)\s*$", re.M)


def tool_consumed(edit: dict, run: Path) -> dict:
    """Did the router read each edited router knob? Evidence: the trial run's own floorplan log.
    A sourced FASTROUTE_TCL that never names the knob means the edit was a tool-level no-op."""
    out = {}
    try:
        log = (run / "logs" / "2_1_floorplan.log").read_text(errors="ignore")
    except OSError:
        log = None
    for k in edit or {}:
        if k not in ROUTER_KNOBS:
            continue
        if log is None:
            out[k] = {"status": "UNKNOWN", "reason": "floorplan_log_absent"}
            continue
        scripts = _SOURCED.findall(log)
        if not scripts:
            out[k] = {"status": "CONSUMED", "evidence": "no FASTROUTE_TCL sourced: floorplan.tcl default branch"}
            continue
        try:
            text = Path(scripts[-1]).read_text(errors="ignore")
        except OSError:
            out[k] = {"status": "UNKNOWN", "reason": "fastroute_tcl_unreadable", "script": scripts[-1]}
            continue
        out[k] = ({"status": "CONSUMED", "script": scripts[-1]} if k in text else
                  {"status": "TOOL_NO_OP", "script": scripts[-1],
                   "evidence": "the sourced FASTROUTE_TCL never reads " + k})
    return out


def corrected(orig: dict, manifest: dict, run: Path) -> dict:
    area = E.check_area(manifest, E.area_metrics(run))
    if orig.get('status') == 'UNVERIFIED' and orig.get('reason'):      # config/binding unresolved
        return {'status': 'UNVERIFIED', 'reason': orig['reason'], 'area': area}
    cfg_ok = (orig.get('config') or {}).get('ok') is True
    status = 'PASS' if cfg_ok and area['ok'] else ('UNVERIFIED' if cfg_ok and not area['violations'] else 'FAIL')
    return {'status': status, 'config': orig.get('config'), 'area': area}


def project_of(state: dict) -> str:
    return f"{state['design']}__u{state['CORE_UTILIZATION']}"


def reevaluate(root: Path) -> dict:
    state = json.loads((root / 'state.json').read_text())
    out = []
    for r in state['records']:
        if r.get('step') == 'baseline':
            out.append({'project': r['project'], 'step': 'baseline', 'status': r.get('status')})
            continue
        man = json.loads((root / 'manifests' / (r['project'] + '.json')).read_text())
        run = root / 'projects' / r['project'] / 'backend' / r['run']
        env1 = corrected(r.get('envelope') or {}, man, run)
        t = r['trial']
        comp = next((c for s in json.loads((root / 'contract.json').read_text())['states'] for c in s['components']
                     if project_of(s) == r['project'] and c['id'] == r['step']), {})
        consumed = tool_consumed(comp.get('edit') or {}, run)
        tool_noop = any(v['status'] == 'TOOL_NO_OP' for v in consumed.values())
        fix = (t.get('kind') == 'action' and t.get('applied') == 'APPLIED' and t.get('congestion') == 'CLEARED'
               and env1['status'] == 'PASS' and not tool_noop)
        out.append({'project': r['project'], 'step': r['step'], 'run': r['run'],
                    'finished_here': E._finished(run),
                    'original': {'envelope': (r.get('envelope') or {}).get('status'), 'fix_credit': t.get('fix_credit'),
                                 'task_success': t.get('task_success')},
                    'corrected': {'envelope': env1['status'], 'fix_credit': fix,
                                  'task_success': fix and t.get('acceptance') == 'ACCEPTED',
                                  'area': {k: {'final': v['final'], 'relative_change': v['relative_change'], 'cap': v['cap']}
                                           for k, v in env1['area']['metrics'].items()},
                                  'area_unverified': env1['area']['unverified']},
                    'tool_consumed': consumed, 'applied_at_tool': 'TOOL_NO_OP' if tool_noop else t.get('applied'),
                    'history_use': ('exclude_as_experience: the router never read the edited knob' if tool_noop
                                    else 'control: logged stop_control_rerun, never an attempt'
                                    if t.get('kind') == 'control' else 'experience'),
                    'changed': ((r.get('envelope') or {}).get('status') != env1['status'] or t.get('fix_credit') != fix
                                or tool_noop),
                    'congestion': t.get('congestion'), 'acceptance': t.get('acceptance'), 'applied': t.get('applied'),
                    'step_check': r.get('step_check')})
    return {'schema': 't2-grt-component-trials-reevaluation-v1', 'reason': 'failure-patterns #61',
            'campaign_status': state.get('status'), 'flows_spent': state.get('flows_spent'), 'trials': out}


def relabel(root: Path) -> dict:
    from ann_v2 import annotate as AN, dev_export as DX, fields as FD, verify as VF
    out = root / 'candidate-history-dev7'
    if out.exists():
        raise RuntimeError('output exists; no overwrite')
    lock = json.loads((root / 'reference/t2-source-lock.json').read_text())
    DX.build(root, ['projects'], root / 'reference/SOURCE-v1', '', set(),
             {e['design'] for e in lock.get('entries', []) if e.get('design')}, out / 'source')
    AN.main(['--source', str(out / 'source'), '--out', str(out / 'ann')])
    rep = VF.verify(out / 'ann/ann_v2_dev.jsonl', out / 'source')
    (out / 'verification.json').write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + '\n')
    return {'annotator': FD.ANNOTATOR_VERSION, 'gate': rep.get('gate'), 'critical': rep.get('critical')}


def trial_overrides(reeval: dict, source_export: Path) -> dict:
    """Shared-history overrides for the trial rows (user rules 2026-10-18): a TOOL_NO_OP row stays in
    the history as its own class `tool_no_op` (a no-op is a category of outcome, never a success and
    never a harm); success also needs area PASS and, when the trial reached signoff, strict
    acceptance -- a cleared congestion with a failed signoff is `cleared_new_failure`."""
    by_session = {}
    for l in Path(source_export).read_text().splitlines():
        if l.strip():
            u = json.loads(l)
            by_session.setdefault((u.get('raw_row') or {}).get('fix_session_id'), []).append(u['unit_id'])
    out = {}
    for t in reeval['trials']:
        if t['step'] == 'baseline':
            continue
        ids = by_session.get(f"ctrial-{t['project']}-{t['step']}") or []
        if len(ids) != 1:
            raise ValueError(f"{t['project']}/{t['step']}: {len(ids)} units")
        c = t['corrected']
        if t.get('applied_at_tool') == 'TOOL_NO_OP':
            out[ids[0]] = {'admitted': False, 'outcome_class': 'tool_no_op',
                           'reason': 'the router never reads the edited knob: ' + json.dumps(t.get('tool_consumed'),
                                                                                              sort_keys=True)}
        elif t.get('congestion') == 'CLEARED' and t.get('acceptance') == 'NOT_ACCEPTED':
            out[ids[0]] = {'admitted': False, 'outcome_class': 'cleared_new_failure',
                           'reason': 'strict acceptance failed after the congestion cleared', 'envelope': c['envelope']}
        elif t.get('congestion') == 'CLEARED' and c['envelope'] != 'PASS':
            out[ids[0]] = {'admitted': False, 'reason': 'area envelope ' + c['envelope'], 'envelope': c['envelope']}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--root', type=Path, required=True)
    a = ap.parse_args(argv)
    rep = reevaluate(a.root)
    rep['labels'] = relabel(a.root)
    (a.root / 'reevaluation.json').write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + '\n')
    print(json.dumps({'labels': rep['labels'], 'changed': [(t['project'], t['step'], t['original'], t['corrected']['envelope'])
                                                            for t in rep['trials'] if t.get('changed')]}, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
