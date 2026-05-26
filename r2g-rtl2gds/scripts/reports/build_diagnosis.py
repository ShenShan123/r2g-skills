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

    # 2. Utilization overflow — require specific error patterns, not independent keywords
    utilization_error = False
    for line in text.splitlines():
        ll = line.lower()
        if ('utilization' in ll and ('exceeds' in ll or '100%' in ll)) or \
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
        if 'no setup violations found' in lower and 'no hold violations found' in lower:
            pass  # Timing is clean
        elif 'setup violation' in lower or 'hold violation' in lower or 'slack (violated)' in lower:
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

    # 10. Synthesis errors
    synth_errors = parse_synth_errors(text)
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
    # Backward-compatible output: top-level fields from first issue, plus issues list
    if issues:
        diagnosis = issues[0].copy()
        diagnosis['issues'] = issues
    else:
        diagnosis = {
            'kind': 'none',
            'summary': 'No known failure signature detected.',
            'suggestion': 'Inspect flow.log manually for details.',
            'issues': []
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(diagnosis, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
