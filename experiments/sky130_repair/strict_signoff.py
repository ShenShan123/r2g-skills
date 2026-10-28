#!/usr/bin/env python3
"""Read actual top-level verdicts; never promote a subcell match to LVS pass.

The frozen checker and result.json stay unchanged. This is a stricter evidence
reader for the SAME acceptance criterion, not a different comparison deck.
"""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def final_lvs(text):
    finals = re.findall(r'^Final result:\s*([^\r\n]+)', text, re.M)
    if not finals:
        return 'unverified_missing_final_verdict'
    value = finals[-1].strip()
    if value in ('Circuits match uniquely.', 'Netlists match uniquely.'):
        return 'clean'
    if value == 'Top level cell failed pin matching.':
        return 'pin_mismatch'
    if 'do not match' in value:
        return 'mismatch'
    return 'unverified_final_verdict'


def inspect_run(run):
    run = Path(run)
    r = json.loads((run/'result.json').read_text())
    f = run/'netgen_lvs.rpt'
    if f.is_file():
        with f.open('rb') as h:
            h.seek(max(0, f.stat().st_size-65536))
            tail = h.read().decode(errors='replace')
        lvs = final_lvs(tail)
    else:
        lvs = 'unverified_missing_report'
    drc = r.get('drc_status')
    report = run/'6_drc.lyrdb'
    violations = None
    if drc == 'clean':
        try:
            tree = ET.parse(report)
            if tree.getroot().tag != 'report-database' or tree.find('items') is None:
                raise ValueError('not a complete KLayout report')
            violations = len(tree.findall('./items/item'))
            drc = 'clean' if violations == 0 else 'violations'
        except (OSError, ET.ParseError, ValueError):
            drc = 'unverified_drc_report'
    gds = run/'6_final.gds'
    clean = (r.get('status') == 'pass' and drc == lvs == 'clean'
             and gds.is_file() and gds.stat().st_size > 0)
    return dict(design=r.get('design'), reported_drc=r.get('drc_status'),
                reported_lvs=r.get('lvs_status'), drc=drc, lvs=lvs,
                drc_items=violations, dual_clean=clean,
                result_sha256=hashlib.sha256((run/'result.json').read_bytes()).hexdigest(),
                lvs_final_source=str(f))
