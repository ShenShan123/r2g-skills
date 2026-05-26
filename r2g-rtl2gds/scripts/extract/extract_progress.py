#!/usr/bin/env python3
"""
Extract ORFS flow progress from backend run directory.
"""
from pathlib import Path
import json
import re
import sys


# ORFS make targets in order
ORFS_STAGES = [
    'synth', 'floorplan', 'place', 'cts', 'route', 'finish'
]


def latest_run(project_root: Path):
    backend = project_root / 'backend'
    if not backend.exists():
        return None
    runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
    return runs[-1] if runs else None


def parse_stage_log(run_dir: Path) -> list:
    """Parse stage_log.jsonl for per-stage timing and status."""
    log_file = run_dir / 'stage_log.jsonl'
    if not log_file.exists():
        return []
    stages = []
    for line in log_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            stages.append({
                'name': entry.get('stage', 'unknown'),
                'status': 'done' if entry.get('status', 1) == 0 else 'failed',
                'elapsed_s': entry.get('elapsed_s'),
            })
        except json.JSONDecodeError:
            continue
    return stages


def parse_congestion(flow_log: Path) -> dict:
    """Extract routing congestion metrics from flow.log."""
    if not flow_log.exists():
        return {}
    text = flow_log.read_text(encoding='utf-8', errors='ignore')
    congestion = {}
    # GRT overflow
    for m in re.finditer(r'Number of overflow:\s*(\d+)', text):
        congestion['grt_overflow'] = int(m.group(1))
    # Total overflow from GRT-0116
    for m in re.finditer(r'Total overflow:\s*(\d+)', text):
        congestion['total_overflow'] = int(m.group(1))
    # NesterovSolve final overflow
    overflow_vals = re.findall(r'\[NesterovSolve\].*overflow:\s*([\d.]+)', text)
    if overflow_vals:
        congestion['placement_overflow'] = float(overflow_vals[-1])
    return congestion


def parse_flow_log_stages(flow_log: Path) -> list:
    """Parse flow.log to determine which ORFS stages completed."""
    if not flow_log.exists():
        return []

    text = flow_log.read_text(encoding='utf-8', errors='ignore')
    stages = []

    stage_patterns = {
        'synth': [r'Synthesis', r'yosys', r'1_synth'],
        'floorplan': [r'Floorplan', r'2_floorplan', r'Init floorplan'],
        'place': [r'Placement', r'3_place', r'Global placement', r'Detail placement'],
        'cts': [r'CTS', r'4_cts', r'Clock Tree Synthesis'],
        'route': [r'Routing', r'5_route', r'Global route', r'Detail route'],
        'finish': [r'Finish', r'6_finish', r'Final report', r'write_gds'],
    }

    for stage_name, patterns in stage_patterns.items():
        found = False
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                found = True
                break
        if found:
            # Check if stage completed or is still running
            status = 'done'
            stages.append({'name': stage_name, 'status': status})

    # Check if the last stage actually succeeded
    if stages and 'error' in text.lower().split('\n')[-50:]:
        stages[-1]['status'] = 'failed'

    return stages


def check_orfs_results(project_root: Path, run_dir: Path) -> list:
    """Check ORFS results directory for stage completion."""
    stages = []
    results = run_dir / 'results'
    if not results.exists():
        return stages

    for stage in ORFS_STAGES:
        stage_files = list(results.rglob(f'*{stage}*'))
        if stage_files:
            stages.append({'name': stage, 'status': 'done'})

    return stages


def tail(path: Path, n: int = 20) -> list:
    if not path.exists():
        return []
    return path.read_text(encoding='utf-8', errors='ignore').splitlines()[-n:]


def main():
    if len(sys.argv) < 3:
        print('usage: extract_progress.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    run = latest_run(project_root)

    result = {'latest_run': None, 'stages': [], 'tails': {}}

    if run is not None:
        result['latest_run'] = str(run)

        # Try stage_log.jsonl first (from incremental flow), then fall back to flow.log parsing
        flow_log = run / 'flow.log'
        stages = parse_stage_log(run)
        if not stages:
            stages = parse_flow_log_stages(flow_log)
        if not stages:
            stages = check_orfs_results(project_root, run)
        result['stages'] = stages

        # Extract congestion metrics
        result['congestion'] = parse_congestion(flow_log)

        # Collect log tails
        for log_name in ['flow.log']:
            log_path = run / log_name
            result['tails'][log_name] = tail(log_path, 30)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
