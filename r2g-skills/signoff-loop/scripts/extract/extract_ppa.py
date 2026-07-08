#!/usr/bin/env python3
"""
Extract PPA metrics from ORFS report files.
Reads reports from the ORFS results/logs directories.
"""
from pathlib import Path
import json
import re
import sys

# Atomic report writes: a kill -9/OOM mid-write must never leave a torn
# reports/*.json for ingest to misread (2026-07-04 robustness audit M1).
import report_io


def parse_area_report(text: str) -> dict:
    """Parse OpenROAD area report."""
    area = {}
    for line in text.splitlines():
        if 'Design area' in line:
            m = re.search(r'Design area\s+([\d.]+)\s+u\^2\s+([\d.]+)%\s+utilization', line)
            if m:
                area['design_area_um2'] = float(m.group(1))
                area['utilization'] = float(m.group(2)) / 100.0
        if 'Total cell area' in line or 'total cell area' in line.lower():
            m = re.search(r'([\d.]+)', line)
            if m:
                area['total_cell_area'] = float(m.group(1))
    return area


def parse_timing_report(text: str) -> dict:
    """Parse OpenSTA timing report for WNS/TNS."""
    timing = {}
    # Look for worst negative slack
    for line in text.splitlines():
        if 'wns' in line.lower():
            m = re.search(r'wns\s+([-\d.]+)', line, re.I)
            if m:
                timing['setup_wns'] = float(m.group(1))
        if 'tns' in line.lower():
            m = re.search(r'tns\s+([-\d.]+)', line, re.I)
            if m:
                timing['setup_tns'] = float(m.group(1))
        if 'slack' in line.lower() and 'MET' in line:
            m = re.search(r'slack\s*\(MET\)\s*([-\d.]+)', line)
            if m:
                timing.setdefault('setup_wns', float(m.group(1)))
        if 'slack' in line.lower() and 'VIOLATED' in line:
            m = re.search(r'slack\s*\(VIOLATED\)\s*([-\d.]+)', line)
            if m:
                timing['setup_wns'] = float(m.group(1))
    return timing


def parse_power_report(text: str) -> dict:
    """Parse OpenROAD power report."""
    power = {}
    for line in text.splitlines():
        if 'Total' in line and ('W' in line or 'mW' in line or 'uW' in line):
            parts = line.split()
            for i, p in enumerate(parts):
                try:
                    val = float(p)
                    # Check if next part is a unit
                    if i + 1 < len(parts):
                        unit = parts[i + 1].lower()
                        if unit in ('w', 'mw', 'uw', 'nw'):
                            multiplier = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(unit, 1.0)
                            power['total'] = val * multiplier
                            break
                except ValueError:
                    continue
        lower = line.lower()
        if 'internal' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['internal'] = val * mult
        if 'switching' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['switching'] = val * mult
        if 'leakage' in lower and 'power' in lower:
            m = re.search(r'([\d.eE+-]+)\s*(w|mw|uw|nw)', lower)
            if m:
                val = float(m.group(1))
                mult = {'w': 1.0, 'mw': 1e-3, 'uw': 1e-6, 'nw': 1e-9}.get(m.group(2), 1.0)
                power['leakage'] = val * mult
    return power


def parse_drc_report(text: str) -> dict:
    """Parse DRC report for violation count."""
    drc = {}
    m = re.search(r'(\d+)\s*violation', text, re.I)
    if m:
        drc['drc_violations'] = int(m.group(1))
    elif 'no violations' in text.lower() or 'clean' in text.lower():
        drc['drc_violations'] = 0
    return drc


# --- Staged setup-slack readers (for the Fmax search & deterioration model) ---
# Per-stage ORFS metrics JSONs live in <run_dir>/logs/. Keys verified against a
# real nangate45 run. 3_4_place_resized is the fallback when 3_5 is absent.
_STAGE_METRIC_FILES = {
    "floorplan": [("2_1_floorplan.json",
                   "floorplan__timing__setup__ws", "floorplan__timing__setup__tns")],
    "place": [("3_5_place_dp.json",
               "detailedplace__timing__setup__ws", "detailedplace__timing__setup__tns"),
              ("3_4_place_resized.json",
               "placeopt__timing__setup__ws", "placeopt__timing__setup__tns")],
}


def _read_stage_json(path: Path, ws_key: str, tns_key: str) -> dict:
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    if ws_key in d:
        out["setup_wns"] = d[ws_key]
    if tns_key in d:
        out["setup_tns"] = d[tns_key]
    return out


def parse_stage_metrics(run_dir, stage: str) -> dict:
    """Return {'setup_wns':.., 'setup_tns':..} for 'floorplan' or 'place' from the
    per-stage metrics JSON under <run_dir>/logs/. Tries fallbacks in order; returns
    {} if nothing readable."""
    logs = Path(run_dir) / "logs"
    for fname, ws_key, tns_key in _STAGE_METRIC_FILES[stage]:
        out = _read_stage_json(logs / fname, ws_key, tns_key)
        if out:
            return out
    return {}


def collect_timing_staged(run_dir) -> dict:
    """{floorplan_setup_ws, place_setup_ws} from whichever stage JSONs exist."""
    staged = {}
    fp = parse_stage_metrics(run_dir, "floorplan")
    pl = parse_stage_metrics(run_dir, "place")
    if "setup_wns" in fp:
        staged["floorplan_setup_ws"] = fp["setup_wns"]
    if "setup_wns" in pl:
        staged["place_setup_ws"] = pl["setup_wns"]
    return staged


def find_reports(project_root: Path) -> dict:
    """Find report files in ORFS backend directory."""
    reports = {}
    # Check backend directory for latest run
    backend = project_root / 'backend'
    if not backend.exists():
        return reports

    runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
    if not runs:
        return reports

    latest = runs[-1]
    reports['run_dir'] = str(latest)

    # Search in copied reports
    for rpt_dir in [latest / 'reports_orfs', latest / 'logs', latest / 'results']:
        if not rpt_dir.exists():
            continue
        for f in rpt_dir.rglob('*'):
            if not f.is_file():
                continue
            name = f.name.lower()
            if 'area' in name and f.suffix in ('.rpt', '.log', '.txt'):
                reports['area_report'] = f
            elif 'timing' in name or 'setup' in name or name.endswith('sta.log'):
                reports.setdefault('timing_report', f)
            elif 'power' in name:
                reports.setdefault('power_report', f)
            elif 'drc' in name:
                reports.setdefault('drc_report', f)

    # Also check flow.log for embedded metrics
    flow_log = latest / 'flow.log'
    if flow_log.exists():
        reports['flow_log'] = flow_log

    return reports


# Main ORFS stages in order; each emits a same-named result ODB on completion.
_ORFS_STAGES = [
    ('synth', '1_synth.odb'),
    ('floorplan', '2_floorplan.odb'),
    ('place', '3_place.odb'),
    ('cts', '4_cts.odb'),
    ('route', '5_route.odb'),
    ('finish', '6_final.odb'),
]


_STAGE_ORDER = [s for s, _ in _ORFS_STAGES]  # synth..finish


def _norm_stage_status(v):
    """0/'pass' -> 'pass'; nonzero/'fail' -> 'fail'; else None.

    The canonical twin is `ingest_run._norm_stage_status` — keep them in sync.
    `run_orfs.sh` records the integer shell exit code (`{"status": 0}`); a few
    legacy writers use the string form. bool is handled before int (subclass).
    """
    if isinstance(v, bool):
        return 'pass' if v else 'fail'
    if isinstance(v, (int, float)):
        return 'pass' if int(v) == 0 else 'fail'
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ('pass', 'ok', 'done', 'success', 'passed', '0'):
            return 'pass'
        if s in ('fail', 'failed', 'error'):
            return 'fail'
    return None


def _progress_from_stage_log(run_dir: Path):
    """Authoritative stage outcome from the run's stage_log.jsonl, or None.

    Preferred over disk ODB-probing: a stage that aborts writes a `*-failed.odb`
    (or nothing is collected back on failure), so probing finds no ODB for the
    failed stage and mis-attributes the abort — e.g. a `place` failure with no
    collected ODBs probes as `orfs_fail_stage='synth'`. The stage_log records
    each stage's real exit code, the same source `ingest_run._derive_orfs_status`
    (and thus the knowledge store) uses, so this keeps the residual honest.
    """
    log = run_dir / 'stage_log.jsonl'
    if not log.is_file():
        return None
    passed: list = []
    fail_stage = None
    for line in log.read_text(errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        st = _norm_stage_status(rec.get('status'))
        if st == 'pass':
            passed.append(rec.get('stage'))
        elif st == 'fail' and fail_stage is None:
            fail_stage = rec.get('stage')
    if fail_stage is None and not passed:
        return None  # empty/unparseable -> let the ODB probe try
    if fail_stage is not None:
        return {'orfs_status': 'fail',
                'orfs_last_stage': passed[-1] if passed else None,
                'orfs_fail_stage': fail_stage}
    passed_set = set(passed)
    if all(s in passed_set for s in _STAGE_ORDER):
        return {'orfs_status': 'complete', 'orfs_last_stage': 'finish',
                'orfs_fail_stage': None}
    nxt = next((s for s in _STAGE_ORDER if s not in passed_set), None)
    return {'orfs_status': 'partial',
            'orfs_last_stage': passed[-1] if passed else None,
            'orfs_fail_stage': nxt}


def detect_orfs_progress(run_dir: Path) -> dict:
    """Classify how far the ORFS backend got.

    Prefers the authoritative `stage_log.jsonl` (real per-stage exit codes);
    falls back to probing stage result ODBs only when no stage_log exists.

    Returns {orfs_status, orfs_last_stage, orfs_fail_stage}:
      - complete : full flow (finish stage passed / 6_final.odb present)
      - partial  : some stages done, the next one is missing -> orfs_fail_stage
      - fail     : a stage aborted (stage_log) or not even synth produced an ODB

    The collected backend flattens results under <run>/results/, but ORFS's
    native layout nests them under results/<platform>/<design>/<variant>/, so we
    match by basename anywhere beneath the run dir. Consumed by the campaign
    driver (run_sky130_design.sh) to label residuals as orfs_<stage> instead of
    the catch-all orfs_incomplete.
    """
    from_log = _progress_from_stage_log(run_dir)
    if from_log is not None:
        return from_log
    present = set()
    rdir = run_dir / 'results'
    search_root = rdir if rdir.is_dir() else run_dir
    if search_root.is_dir():
        for name in {odb for _, odb in _ORFS_STAGES}:
            if next(search_root.rglob(name), None) is not None:
                present.add(name)
    last_stage = None
    fail_stage = None
    for stage, odb in _ORFS_STAGES:
        if odb in present:
            last_stage = stage
        elif fail_stage is None:
            fail_stage = stage  # first missing stage after the last completed one
    if last_stage == 'finish':
        return {'orfs_status': 'complete', 'orfs_last_stage': 'finish',
                'orfs_fail_stage': None}
    if last_stage is None:
        return {'orfs_status': 'fail', 'orfs_last_stage': None,
                'orfs_fail_stage': 'synth'}
    return {'orfs_status': 'partial', 'orfs_last_stage': last_stage,
            'orfs_fail_stage': fail_stage}


def main():
    argv = sys.argv[1:]
    stage_arg = None
    if "--stage" in argv:
        i = argv.index("--stage")
        stage_arg = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        print('usage: extract_ppa.py <project-root> <output.json> [--stage floorplan|place]',
              file=sys.stderr)
        sys.exit(1)
    project_root = Path(argv[0])
    out_path = Path(argv[1])

    reports = find_reports(project_root)

    ppa = {
        'summary': {
            'area': {},
            'timing': {},
            'power': {},
            'drc': {},
        },
        'geometry': {},
        'run_dir': reports.get('run_dir'),
    }

    # ORFS stage progress / failed stage (top-level so the campaign driver can
    # read it via a flat key lookup). Absent run_dir -> nothing ran.
    if reports.get('run_dir'):
        ppa.update(detect_orfs_progress(Path(reports['run_dir'])))
    else:
        ppa.update({'orfs_status': 'fail', 'orfs_last_stage': None,
                    'orfs_fail_stage': 'synth'})

    for key, parser in [
        ('area_report', parse_area_report),
        ('timing_report', parse_timing_report),
        ('power_report', parse_power_report),
        ('drc_report', parse_drc_report),
    ]:
        rpt_path = reports.get(key)
        if rpt_path and Path(rpt_path).exists():
            text = Path(rpt_path).read_text(encoding='utf-8', errors='ignore')
            category = key.replace('_report', '')
            ppa['summary'][category].update(parser(text))

    # Try parsing flow.log for metrics if specific reports weren't found
    flow_log = reports.get('flow_log')
    if flow_log and Path(flow_log).exists():
        text = Path(flow_log).read_text(encoding='utf-8', errors='ignore')
        if not ppa['summary']['area']:
            ppa['summary']['area'] = parse_area_report(text)
        if not ppa['summary']['timing']:
            ppa['summary']['timing'] = parse_timing_report(text)
        if not ppa['summary']['power']:
            ppa['summary']['power'] = parse_power_report(text)

    # Extract detailed geometry from 6_report.json
    if reports.get('run_dir'):
        report_json = Path(reports['run_dir']) / 'logs' / '6_report.json'
        if report_json.exists():
            rj = json.loads(report_json.read_text(encoding='utf-8', errors='ignore'))
            geo = {}
            key_map = {
                'die_area_um2': 'finish__design__die__area',
                'core_area_um2': 'finish__design__core__area',
                'utilization': 'finish__design__instance__utilization',
                'instance_count': 'finish__design__instance__count',
                'stdcell_count': 'finish__design__instance__count__stdcell',
                'stdcell_area_um2': 'finish__design__instance__area__stdcell',
                'macro_count': 'finish__design__instance__count__macros',
                'macro_area_um2': 'finish__design__instance__area__macros',
                'io_count': 'finish__design__io',
                'rows': 'finish__design__rows',
                'sites': 'finish__design__sites',
                'clock_buffer_count': 'finish__design__instance__count__class:clock_buffer',
                'sequential_count': 'finish__design__instance__count__class:sequential_cell',
                'warnings': 'finish__flow__warnings__count',
                'errors': 'finish__flow__errors__count',
            }
            for out_key, json_key in key_map.items():
                if json_key in rj:
                    geo[out_key] = rj[json_key]
            ppa['geometry'] = geo

            # Extract timing from 6_report.json — these values are authoritative
            # and OVERWRITE the flow.log-parsed values which can be wrong
            # (e.g. regex matches ORFS command '-repair_tns 100' instead of actual TNS).
            timing_map = {
                'setup_wns': 'finish__timing__setup__ws',
                'setup_tns': 'finish__timing__setup__tns',
                'hold_wns': 'finish__timing__hold__ws',
                'hold_tns': 'finish__timing__hold__tns',
                'clock_skew_setup': 'finish__clock__skew__setup',
                'clock_skew_hold': 'finish__clock__skew__hold',
                'setup_violation_count': 'finish__timing__drv__setup_violation_count',
                'hold_violation_count': 'finish__timing__drv__hold_violation_count',
                'max_cap_violations': 'finish__timing__drv__max_cap',
                'max_slew_violations': 'finish__timing__drv__max_slew',
            }
            report_timing = {}
            for out_key, json_key in timing_map.items():
                if json_key in rj:
                    report_timing[out_key] = rj[json_key]
            if report_timing:
                ppa['summary']['timing'] = report_timing

            # Extract power from 6_report.json — overwrites flow.log-parsed values.
            power_map = {
                'total_power_w': 'finish__power__total',
                'internal_power_w': 'finish__power__internal__total',
                'switching_power_w': 'finish__power__switching__total',
                'leakage_power_w': 'finish__power__leakage__total',
            }
            report_power = {}
            for out_key, json_key in power_map.items():
                if json_key in rj:
                    report_power[out_key] = rj[json_key]
            if report_power:
                ppa['summary']['power'] = report_power

    # Staged setup slacks (for the deterioration model) + optional per-stage override.
    if reports.get('run_dir'):
        staged = collect_timing_staged(reports['run_dir'])
        if 'setup_wns' in ppa['summary']['timing']:
            staged['finish_setup_ws'] = ppa['summary']['timing']['setup_wns']
        if staged:
            ppa['summary']['timing_staged'] = staged
        if stage_arg in ('floorplan', 'place'):
            sm = parse_stage_metrics(reports['run_dir'], stage_arg)
            if sm:
                # For a place-only Fmax probe there is no finish/6_report; surface
                # the requested stage's slack in the standard summary.timing shape.
                ppa['summary']['timing'] = sm

    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_io.write_json_atomic(out_path, ppa)
    print(out_path)


if __name__ == '__main__':
    main()
