#!/usr/bin/env python3
"""
Diagnosis Engine - parse flow logs and provide actionable suggestions.
Adapted for OpenROAD-flow-scripts (ORFS).
"""
from pathlib import Path
import json
import re
import sys


def load_text(path: Path) -> str:
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8', errors='ignore')


def parse_lint_errors(text: str) -> list:
    errors = []
    for line in text.splitlines():
        if 'Error-' in line or '%Error' in line:
            match = re.search(r'%?Error-(\w+):.*?:(\d+):(\d+):(.+)', line)
            if match:
                errors.append({
                    'type': match.group(1),
                    'line': int(match.group(2)),
                    'col': int(match.group(3)),
                    'msg': match.group(4).strip()
                })
    return errors


def parse_synth_errors(text: str) -> list:
    errors = []
    for line in text.splitlines():
        if 'ERROR' in line or 'Error:' in line:
            errors.append(line.strip())
    return errors


def section_text(text: str, name: str) -> str:
    """Return the body of the named log section ('=== name === ... ') or ''.
    main() joins each log as '=== <name> ===\\n<content>'. Scoping a parser to
    its OWN section stops cross-contamination — a route/DRC/LVS 'ERROR' line in
    flow.log must NOT be mislabeled a synthesis error (failure-patterns.md #38).
    Mirrors how the DRC (#8) and make-error (#9) checks already scope."""
    for section in text.split('=== '):
        if section.startswith(name):
            parts = section.split('===', 1)
            return parts[1].lstrip('\n') if len(parts) > 1 else ''
    return ''


def detect_issues(text: str, project: Path) -> list:
    issues = []

    ppa_file = project / 'reports' / 'ppa.json'
    ppa_data = None
    if ppa_file.exists():
        try:
            ppa_data = json.loads(ppa_file.read_text(encoding='utf-8', errors='ignore'))
        except (json.JSONDecodeError, TypeError):
            pass
    lower = text.lower()

    # 1. Lint errors
    lint_errors = parse_lint_errors(text)
    if lint_errors:
        suggestions = []
        error_types = set(e['type'] for e in lint_errors)
        if 'BLKANDNBLK' in error_types:
            suggestions.append('Mixed blocking (=) and non-blocking (<=) assignments. Use <= in sequential logic.')
        if 'BLKSEQ' in error_types:
            suggestions.append('Blocking assignment in sequential logic. Use non-blocking (<=) instead.')
        if 'UNDRIVEN' in error_types:
            suggestions.append('Undriven signal. Check all outputs have assignments.')
        if 'MULTIDRIVEN' in error_types:
            suggestions.append('Multi-driven signal. Check for multiple always blocks driving the same signal.')
        issues.append({
            'kind': 'lint_errors',
            'summary': f'Found {len(lint_errors)} lint errors',
            'suggestion': ' '.join(suggestions) if suggestions else 'Review and fix RTL code.',
            'details': lint_errors[:5]
        })

    # 1b. IO-pin capacity overflow (PPL-0024) — the die PERIMETER is too short to
    # seat the design's IO pins. Detected BEFORE the utilization rule so a
    # pin-overflow abort surfaces as its OWN first-class kind (whose lever is
    # ENLARGING the die perimeter), never as placement_utilization_overflow
    # (whose lever is lowering density) — the two aborts have DISTINCT repairs, so
    # a consumer learning the wrong family fixes the wrong knob (2026-07-16
    # full-pipeline #12). engineer_loop._is_ppl0024 / _ppl0024_required_perimeter
    # are the runtime detectors; this mirrors their signal + perimeter regex.
    if 'ppl-0024' in lower or ('io pins' in lower and 'available positions' in lower):
        pin_issue = {
            'kind': 'io_pin_capacity_overflow',
            'summary': "IO pins exceed the die perimeter's available pin positions (PPL-0024).",
            'suggestion': 'Enlarge the die perimeter (larger DIE_AREA/CORE_AREA) or '
                          'reduce the number of top-level IO pins. This is a perimeter '
                          'shortfall, not a placement-density problem — lowering '
                          'CORE_UTILIZATION alone will not fix it.'
        }
        # Same regex engineer_loop._ppl0024_required_perimeter uses, extended to
        # also capture the current perimeter so both ride the issue payload.
        m = re.search(r'die perimeter from ([\d.]+)\s*um to ([\d.]+)\s*um', text)
        if m:
            pin_issue['current_perimeter_um'] = float(m.group(1))
            pin_issue['required_perimeter_um'] = float(m.group(2))
        issues.append(pin_issue)

    # 2. Utilization overflow — require a real placement-overflow error CODE or an
    # explicit '[error … utilization]' / 'utilization … exceeds' line. The bare
    # '100%' trigger was dropped: Yosys prints a healthy INFO line 'Design area NNN
    # um^2 ~100% utilization', which false-positived here, while the genuine
    # overflow aborts carry codes (DPL-0036/FLW-0024/GPL-0053) that do NOT contain
    # the word 'utilization' at all and were missed by the code-less branch
    # (2026-07-16 full-pipeline #12).
    utilization_error = False
    for line in text.splitlines():
        ll = line.lower()
        if ('dpl-0036' in ll or 'flw-0024' in ll or 'gpl-0053' in ll) or \
           ('utilization' in ll and 'exceeds' in ll) or \
           ('[error' in ll and 'utilization' in ll):
            utilization_error = True
            break
    if utilization_error:
        issues.append({
            'kind': 'placement_utilization_overflow',
            'summary': 'Placement failed because utilization is too high.',
            'suggestion': 'Reduce CORE_UTILIZATION in config.mk or simplify the design.'
        })

    # 3. Floorplan too small
    if 'not enough area' in lower or 'core area too small' in lower:
        issues.append({
            'kind': 'floorplan_too_small',
            'summary': 'Core area is too small for the design.',
            'suggestion': 'Increase die area in config.mk or reduce CORE_UTILIZATION.'
        })

    # 4. Clock not found
    if 'cannot find port' in lower and 'clock' in lower:
        issues.append({
            'kind': 'clock_port_missing',
            'summary': 'Clock port specified in SDC not found in design.',
            'suggestion': 'Check clk_port_name in constraint.sdc matches the RTL clock port name.'
        })

    # 5. No cells after synthesis
    if 'no cells mapped' in lower or 'empty design' in lower:
        issues.append({
            'kind': 'empty_synthesis',
            'summary': 'Synthesis produced no cells.',
            'suggestion': 'Check that DESIGN_NAME in config.mk matches the top module name in RTL.'
        })

    # 6. Testbench failure
    if 'tb_fail' in lower or 'assertion failed' in lower or 'test failed' in lower:
        issues.append({
            'kind': 'testbench_failure',
            'summary': 'Simulation failed due to testbench assertions.',
            'suggestion': 'Inspect assertion timing and expected cycle alignment.'
        })

    # 7. Timing violations (log-based) — skip when PPA data is available,
    # because flow.log contains intermediate repair messages that are not
    # authoritative. The PPA-based checks (below) use 6_report.json which
    # reflects the final state after all repairs.
    if not ppa_data:
        # Neutralize the clean-timing negations BEFORE the substring scan: the
        # phrase 'no setup violations found' CONTAINS the alarm substring 'setup
        # violation' (violations = violation + s), so a clean STA report
        # false-positived as a timing_violation whenever the paired hold-clean
        # line was absent (2026-07-16 full-pipeline #12). Scrub, then match.
        scrubbed = re.sub(r'no (setup|hold) violations found', '', lower)
        if 'setup violation' in scrubbed or 'hold violation' in scrubbed \
                or 'slack (violated)' in scrubbed:
            issues.append({
                'kind': 'timing_violation',
                'summary': 'Timing violations detected.',
                'suggestion': 'Try increasing clock period in constraint.sdc or optimizing RTL.'
            })

    # 8. DRC errors — check 6_drc_count.rpt section (authoritative DRC count)
    drc_count = -1
    for section in text.split('=== '):
        if section.startswith('6_drc_count.rpt'):
            for line in section.strip().splitlines()[1:]:
                line = line.strip()
                if line.isdigit():
                    drc_count = int(line)
                    break
            break
    if drc_count > 0:
        issues.append({
            'kind': 'drc_errors',
            'summary': f'{drc_count} DRC violations found.',
            'suggestion': 'Review routing density and spacing constraints. Run extract_drc.py for detailed violation categories.'
        })

    # 8b. LVS mismatch
    if "netlists don't match" in lower or 'netlists do not match' in lower or 'lvs mismatch' in lower:
        issues.append({
            'kind': 'lvs_mismatch',
            'summary': 'LVS mismatch — layout does not match schematic netlist.',
            'suggestion': 'Check for missing connections, extra devices, or port mismatches. Review 6_lvs.lvsdb for specifics.'
        })

    # 8c. RCX extraction failure
    if 'extract_parasitics' in lower and ('error' in lower or 'fail' in lower):
        issues.append({
            'kind': 'rcx_failure',
            'summary': 'OpenRCX parasitic extraction failed.',
            'suggestion': 'Verify RCX rules file exists for this platform. Check that the ODB file is valid.'
        })

    # 9. Make/build errors — only match if flow.log section has make error at end
    make_error_found = False
    flow_section = ''
    for section in text.split('=== '):
        if section.startswith('flow.log'):
            flow_section = section
            break
    if flow_section:
        flow_lines = flow_section.strip().splitlines()
        tail = '\n'.join(flow_lines[-50:]).lower()
        if 'make: ***' in tail or ('error' in tail and 'exit status' in tail):
            make_error_found = True
    if make_error_found:
        issues.append({
            'kind': 'make_error',
            'summary': 'ORFS make target failed.',
            'suggestion': 'Check flow.log for the specific failing stage and error details.'
        })

    # 10. Synthesis errors — scoped to the synth.log section ONLY (#38). Feeding
    # the full concatenated text mislabeled a route '[ERROR GRT-…]' or an LVS
    # mismatch line as a synthesis error (a false-positive diagnosis that sends a
    # fixer down the wrong lever). Genuine synth failures also have dedicated
    # signatures above (empty_synthesis #5, make_error #9, clock_port_missing #4).
    synth_errors = parse_synth_errors(section_text(text, 'synth.log'))
    if synth_errors:
        issues.append({
            'kind': 'synthesis_errors',
            'summary': f'Found {len(synth_errors)} synthesis errors',
            'suggestion': 'Check RTL syntax and constraint files.',
            'details': synth_errors[:5]
        })

    # === Timing checks from ppa.json (WNS + TNS) ===
    if ppa_data:
        timing = ppa_data.get('summary', {}).get('timing', {})
        wns = timing.get('setup_wns')
        tns = timing.get('setup_tns')
        count = timing.get('setup_violation_count', 'N/A')

        # 11. Unconstrained timing (WNS = 1e+39)
        if wns is not None and isinstance(wns, (int, float)) and wns > 1e+30:
            issues.append({
                'kind': 'unconstrained_timing',
                'summary': f'Timing is unconstrained (WNS={wns}). Clock constraints not applied.',
                'suggestion': 'SDC clock port name likely does not match RTL port. '
                              'Run validate_config.py to identify the mismatch.'
            })

        # 11b. Severe setup violations (WNS < -2.0 OR TNS < -100.0)
        elif ((wns is not None and isinstance(wns, (int, float)) and wns < -2.0) or
              (tns is not None and isinstance(tns, (int, float)) and tns < -100.0)):
            wns_s = f'{wns:.4f}' if isinstance(wns, (int, float)) else 'N/A'
            tns_s = f'{tns:.4f}' if isinstance(tns, (int, float)) else 'N/A'
            issues.append({
                'kind': 'severe_setup_violation',
                'summary': f'Severe setup timing violations: WNS={wns_s}ns, TNS={tns_s}ns, count={count}.',
                'suggestion': 'Timing is far from closure. Run check_timing.py for '
                              'numbered fix options. Do not proceed to signoff without user approval.'
            })

        # 11c. Minor setup violations (WNS < 0 OR TNS < 0, but not severe)
        elif ((wns is not None and isinstance(wns, (int, float)) and wns < 0) or
              (tns is not None and isinstance(tns, (int, float)) and tns < 0)):
            wns_s = f'{wns:.4f}' if isinstance(wns, (int, float)) else 'N/A'
            tns_s = f'{tns:.4f}' if isinstance(tns, (int, float)) else 'N/A'
            issues.append({
                'kind': 'minor_setup_violation',
                'summary': f'Minor setup timing violations: WNS={wns_s}ns, TNS={tns_s}ns, count={count}.',
                'suggestion': 'Auto-fixable: increase clock period and re-run backend. '
                              'Run check_timing.py for exact suggested values.'
            })

        # 12. Hold timing violations
        hold_tns = timing.get('hold_tns')
        if hold_tns is not None and isinstance(hold_tns, (int, float)) and hold_tns < -0.01:
            hold_count = timing.get('hold_violation_count', 'unknown')
            issues.append({
                'kind': 'hold_timing_violations',
                'summary': f'Hold timing violations: hold_tns={hold_tns:.4f}ns, count={hold_count}.',
                'suggestion': 'For large designs with macros, caused by CTS clock skew. '
                              'Try HOLD_SLACK_MARGIN=0.1 in config.mk.'
            })

    # 13. Routing congestion (GRT-0116)
    for line in text.splitlines():
        if 'GRT-0116' in line or ('global routing' in line.lower() and 'congestion' in line.lower()):
            issues.append({
                'kind': 'routing_congestion',
                'summary': 'Global routing failed due to congestion.',
                'suggestion': 'Reduce CORE_UTILIZATION by 5-10% or add '
                              'ROUTING_LAYER_ADJUSTMENT=0.10 to config.mk.'
            })
            break

    return issues


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='ignore'))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _latest_run(project: Path):
    backend = project / 'backend'
    if not backend.exists():
        return None
    runs = sorted([d for d in backend.iterdir()
                   if d.is_dir() and d.name.startswith('RUN_')])
    return runs[-1] if runs else None


def read_stage_summary(project: Path):
    """Per-stage durations from the latest backend RUN_*/stage_log.jsonl — the
    dimension the raw diagnosis lacked (codex #7). Returns (stages, total_s)."""
    run = _latest_run(project)
    if run is None:
        return [], 0
    slog = run / 'stage_log.jsonl'
    if not slog.exists():
        return [], 0
    stages, total = [], 0
    for line in slog.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if 'stage' not in rec or 'status' not in rec:  # skip any non-stage rows
            continue
        el = rec.get('elapsed_s') or 0
        entry = {'stage': rec.get('stage'), 'status': rec.get('status'), 'elapsed_s': el}
        if rec.get('artifact'):
            entry['artifact'] = rec['artifact']
        stages.append(entry)
        try:
            total += int(el)
        except (TypeError, ValueError):
            pass
    return stages, total


def read_fix_summary(project: Path):
    """Repair repetition counts from reports/fix_log.jsonl (codex #7)."""
    flog = project / 'reports' / 'fix_log.jsonl'
    if not flog.exists():
        return {'fix_iterations': 0, 'fix_iters_to_clean': None}
    cleared, iters = [], 0
    for line in flog.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not rec.get('iter'):
            continue
        iters += 1
        if rec.get('verdict') == 'cleared':
            try:
                cleared.append(int(rec['iter']))
            except (TypeError, ValueError):
                pass
    return {'fix_iterations': iters,
            'fix_iters_to_clean': max(cleared) if cleared else None}


def build_run_summary(project: Path):
    """Consolidate the five dimensions the raw logs scatter (codex #7): stage
    durations, repair repetitions, and DRC/LVS/route/timing status — so
    diagnosis.json is a single structured run summary, not just a failure list."""
    stages, total = read_stage_summary(project)
    summary = {'stages': stages, 'total_elapsed_s': total}
    summary.update(read_fix_summary(project))
    signoff = {}
    rep = project / 'reports'
    for name in ('drc', 'lvs', 'route'):
        d = _load_json(rep / f'{name}.json')
        if isinstance(d, dict):
            signoff[name] = d.get('status')
    tc = _load_json(rep / 'timing_check.json')
    if isinstance(tc, dict) and tc.get('tier'):
        signoff['timing'] = tc.get('tier')
    ppa = _load_json(rep / 'ppa.json')
    if isinstance(ppa, dict) and ppa.get('orfs_status'):
        signoff['orfs_status'] = ppa.get('orfs_status')
        if ppa.get('orfs_fail_stage'):
            signoff['orfs_fail_stage'] = ppa.get('orfs_fail_stage')
    if signoff:
        summary['signoff'] = signoff
    # Echo the terminal antenna-repair verdict written by fix_signoff.sh so the
    # structured summary carries "the fix loop already gave up" (codex-debug
    # 2026-07-13 #4). Read-only echo — the marker is already the source of truth
    # for signoff_gate.py + the ingest fix-event, so this creates no new event.
    ant = _load_json(rep / 'antenna_nonconverged.json')
    if isinstance(ant, dict) and ant:
        summary['antenna_nonconverged'] = ant
    return summary


def _orfs_fallback_kind(run_summary):
    """When no text-log signature matched but the ORFS stage ledger shows the
    backend flow did not complete, return a stage-named diagnosis so
    diagnosis.json no longer reports `kind:none` for a real backend abort or
    timeout (codex-debug 2026-07-13 #4 — e.g. a stage killed at ORFS_TIMEOUT
    leaves no `make: *** Error` line, so every text rule misses it).

    Presentation-layer ONLY. `ingest_run.py` derives `orfs_status`/`fail_stage`
    and the `orfs-fail-<stage>` failure_event INDEPENDENTLY from stage_log, and
    builds failure_events solely from `diag['issues']` — which this never
    touches — so the fallback creates and duplicates no failure_event. It also
    subsumes the reviewer's proposed `route_completed_but_finish_missing`
    (that is simply orfs_status='fail', orfs_fail_stage='finish').
    Returns a diagnosis dict or None."""
    signoff = (run_summary or {}).get('signoff') or {}
    status = signoff.get('orfs_status')
    stage = signoff.get('orfs_fail_stage')
    if status == 'fail':
        where = f" at stage '{stage}'" if stage else ''
        return {
            'kind': 'orfs_stage_failed',
            'summary': f"ORFS backend flow failed{where} with no distinctive log "
                       "signature (e.g. a stage killed at ORFS_TIMEOUT, or an "
                       "opaque abort).",
            'suggestion': "Inspect the failed stage's log under backend/RUN_*/ "
                          "and reports/ppa.json; a stage that exceeded ORFS_TIMEOUT "
                          "leaves no `make` error line (failure-patterns #40).",
        }
    if status == 'partial':
        where = f" after stage '{stage}'" if stage else ''
        return {
            'kind': 'orfs_stage_incomplete',
            'summary': f"ORFS backend flow is incomplete{where}: it did not reach "
                       "the finish stage / 6_final artifacts.",
            'suggestion': "Resume from the failed stage (FROM_STAGE=<stage>) or "
                          "inspect backend/RUN_*/stage_log.jsonl for the last "
                          "completed stage.",
        }
    return None


def main():
    if len(sys.argv) < 3:
        print('usage: build_diagnosis.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project = Path(sys.argv[1])
    out = Path(sys.argv[2])

    texts = []

    # Collect backend logs
    backend = project / 'backend'
    if backend.exists():
        runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
        if runs:
            latest = runs[-1]
            flow_log = latest / 'flow.log'
            if flow_log.exists():
                texts.append(f'=== flow.log ===\n{load_text(flow_log)}')

    # Collect lint/sim/synth/drc/lvs/rcx logs
    for log_path in [
        project / 'lint' / 'lint.log',
        project / 'sim' / 'sim.log',
        project / 'synth' / 'synth.log',
        project / 'drc' / '6_drc.log',
        project / 'drc' / 'drc_run.log',
        project / 'drc' / '6_drc_count.rpt',
        project / 'lvs' / '6_lvs.log',
        project / 'lvs' / 'lvs_run.log',
        project / 'rcx' / 'rcx.log',
    ]:
        if log_path.exists():
            texts.append(f'=== {log_path.name} ===\n{load_text(log_path)}')

    full_text = '\n'.join(texts)
    issues = detect_issues(full_text, project)
    # Consolidated run summary (codex #7): stage durations + repair repetitions +
    # signoff status, so diagnosis.json is the single structured run summary the
    # suggestion asks for — not just a failure list to hand-stitch with the DB.
    # Built BEFORE the kind decision so the fallback can consult orfs_status.
    run_summary = build_run_summary(project)
    # Backward-compatible output: top-level fields from first issue, plus issues list
    if issues:
        diagnosis = issues[0].copy()
        diagnosis['issues'] = issues
    else:
        # No text-log signature matched. Before declaring `kind:none`, consult
        # the ORFS stage ledger: a backend abort/timeout with no `make` error
        # line is a real failure the text rules cannot see (codex-debug #4).
        diagnosis = _orfs_fallback_kind(run_summary) or {
            'kind': 'none',
            'summary': 'No known failure signature detected.',
            'suggestion': 'Inspect flow.log manually for details.',
        }
        diagnosis['issues'] = []

    diagnosis['run_summary'] = run_summary

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(diagnosis, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
