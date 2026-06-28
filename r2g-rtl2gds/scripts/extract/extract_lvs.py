#!/usr/bin/env python3
"""
Extract LVS results from KLayout lvsdb report.
Produces a JSON summary with match/mismatch status and details.
"""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET


def parse_lvsdb(lvs_dir: Path) -> dict:
    """Parse KLayout lvsdb (XML) for LVS comparison results."""
    lvsdb_file = lvs_dir / '6_lvs.lvsdb'
    if not lvsdb_file.exists():
        return {}

    result = {}
    try:
        tree = ET.parse(lvsdb_file)
        root = tree.getroot()

        # Look for status elements
        for status_el in root.iter('status'):
            if status_el.text:
                result['raw_status'] = status_el.text.strip()

        # Count mismatches
        mismatches = 0
        for mismatch in root.iter('mismatch'):
            mismatches += 1
        result['mismatch_count'] = mismatches

        # Look for net/device counts
        for net_el in root.iter('net_count'):
            if net_el.text:
                result.setdefault('net_count', int(net_el.text))
        for dev_el in root.iter('device_count'):
            if dev_el.text:
                result.setdefault('device_count', int(dev_el.text))
        for pin_el in root.iter('pin_count'):
            if pin_el.text:
                result.setdefault('pin_count', int(pin_el.text))

    except ET.ParseError:
        # KLayout lvsdb may use text format (#%lvsdb-klayout), not XML
        text = lvsdb_file.read_text(encoding='utf-8', errors='ignore')
        lower_text = text.lower()
        if 'mismatch' in lower_text:
            result['raw_status'] = 'text_mismatch_found'
            result['mismatch_count'] = sum(1 for line in text.splitlines() if 'mismatch' in line.lower())
        elif "don't match" in lower_text or 'not match' in lower_text:
            result['raw_status'] = 'text_not_match'
            result['mismatch_count'] = -1  # unknown count, but known mismatch
        elif 'match' in lower_text:
            result['raw_status'] = 'text_match_found'
            result['mismatch_count'] = 0
        else:
            result['raw_status'] = 'text_unparsed'
            # Do NOT set mismatch_count — leave it absent so status logic doesn't assume clean

    return result


# --- Mismatch-class classification (s-expression lvsdb) ----------------------
# Distinguishes the dominant LVS "fail" sub-causes so the diagnoser can emit a
# precise, honest residual instead of a generic "operator review".  See
# references/failure-patterns.md "LVS symmetric-matcher residual".
#
# The discriminator is NET BALANCE + DEVICE-COUNT AGREEMENT, not "zero net
# deltas" (validated 2026-06-03 corpus triage). KLayout-0.30.7's netlist comparer
# cannot uniquely fingerprint topologically identical instances in *symmetric*
# structures (register files `MEMORY[][]`, parallel NAND/XOR/parity trees, crypto
# mixing rounds, replicated bit-slices). When it gives up it leaves the unmatched
# nets PERFECTLY BALANCED (schematic-only count == layout-only count) with EVERY
# device matched (device_mismatches == 0) — the layout is electrically correct;
# only the instance/net *assignment* is ambiguous. That is `symmetric_matcher`
# (a tool limitation, not a defect). Earlier the label required net_mismatches==0,
# which under-reported: aes_core (8+8 balanced), vlsi_axi_slave (40+40),
# iccad2017_unit5_F (64+64) all carry balanced unmatched nets yet are clean layouts.
#
# A GENUINE defect breaks the balance: more layout nets than schematic (or vice
# versa), a paired-but-mismatching net `net(N M mismatch)`, a device-count delta,
# or an explicit "not matching any net" error. Those stay `generic` (operator
# review) and the "not matching any net" signature forces `real_connectivity` —
# both wb2axip_axi2axilite (net open: +1 layout net) and wb2axip_axilsingle
# (16 bus opens: 104 vs 120) are real defects caught this way. real_connectivity
# takes priority so a benign label is never applied to a real bug.
_NET_SCHEM_ONLY_RE = re.compile(r"net\(\(\)\s+\d+\s+mismatch\)")   # () N -> in schematic only
_NET_LAYOUT_ONLY_RE = re.compile(r"net\(\d+\s+\(\)\s+mismatch\)")  # N () -> in layout only
_NET_PAIRED_RE = re.compile(r"net\(\d+\s+\d+\s+mismatch\)")        # N M -> paired but mismatching (genuine delta)
# Back-compat alias: total unmatched nets (schematic-only + layout-only).
_NET_MISMATCH_RE = re.compile(r"net\(\(\)\s+\d+\s+mismatch\)|net\(\d+\s+\(\)\s+mismatch\)")
_DEVICE_MISMATCH_RE = re.compile(
    r"device\(\(\)\s+\d+\s+mismatch\)|device\(\d+\s+\(\)\s+mismatch\)|device\(\d+\s+\d+\s+mismatch\)"
)
_CIRCUIT_SWAP_RE = re.compile(r"circuit\(\d+\s+\d+\s+mismatch\)")
_AMBIGUOUS_RE = re.compile(r"ambiguous group of nets")
_NOT_MATCHING_RE = re.compile(r"is not matching any net", re.I)


def classify_lvs_mismatch(lvs_dir: Path) -> dict:
    """Classify a 'fail' lvsdb into symmetric_matcher / real_connectivity / generic.

    Returns {} when no lvsdb is present (e.g. crash before write).
    """
    lvsdb_file = lvs_dir / '6_lvs.lvsdb'
    if not lvsdb_file.exists():
        return {}
    text = lvsdb_file.read_text(encoding='utf-8', errors='ignore')
    schem_only = len(_NET_SCHEM_ONLY_RE.findall(text))
    layout_only = len(_NET_LAYOUT_ONLY_RE.findall(text))
    paired_mm = len(_NET_PAIRED_RE.findall(text))
    device_mm = len(_DEVICE_MISMATCH_RE.findall(text))
    net_mismatches = schem_only + layout_only  # total unmatched (back-compat)
    circuit_swaps = len(_CIRCUIT_SWAP_RE.findall(text))
    ambiguous = len(_AMBIGUOUS_RE.findall(text))
    if _NOT_MATCHING_RE.search(text):
        cls = 'real_connectivity'
    elif (schem_only == layout_only and paired_mm == 0 and device_mm == 0
          and (ambiguous > 0 or circuit_swaps > 0)):
        # Balanced unmatched nets, all devices match, only instance/net
        # ambiguity remains -> KLayout-0.30.7 symmetric-matcher limit.
        cls = 'symmetric_matcher'
    else:
        cls = 'generic'
    return {
        'mismatch_class': cls,
        'net_mismatches': net_mismatches,
        'net_mismatches_schematic_only': schem_only,
        'net_mismatches_layout_only': layout_only,
        'paired_net_mismatches': paired_mm,
        'device_mismatches': device_mm,
        'circuit_swaps': circuit_swaps,
        'ambiguous_groups': ambiguous,
    }


_CRASH_RE = re.compile(
    r"signal number:\s*\d+|segmentation|sigsegv|sort_circuit|gen_log_entry"
    r"|ruby_run_node|klayout_crash\.log"
    # KLayout INTERNAL error in a POST-compare step (writing the LVS database) — a
    # retry-fixable tool crash, NOT a layout mismatch: the lvsdb-writer net2id assert
    # ('dbLayoutVsSchematicWriter.cc ... i != net2id.end ()') / 'Internal error ... in
    # Executable::cleanup'. Misread as lvs=fail before (2026-06-28 PicoRV32_..._fifo_basic).
    r"|dblayoutvsschematicwriter|net2id\.end|internal error.*executable::cleanup",
    re.I,
)

_DEVICE_EXTRACT_RE = re.compile(
    r'"extract_devices"\s+in:\s+FreePDK45|"netlist"\s+in:\s+FreePDK45'
    r"|extract_devices|\"netlist\"",
    re.I,
)

# KLayout 0.30.7's SPICE reader aborts (no verdict) when the CDL contains an
# instance name it mis-tokenizes — notably escaped-bracket / negative-index names
# like `Xr_CS_Inactive_Count\[-1\]$_DFFE_PN0P_` produced by a `[-1]` bit-blast.
# It throws `Pin count mismatch (N expected, got N+1) ... in Netlist::read` BEFORE
# any comparison, so the layout is never assessed. Distinct from a layout
# mismatch — surfaced as reason `cdl_parse_error`. See references/failure-patterns.md
# "LVS CDL parse error (escaped-bracket / negative-index instance names)".
_CDL_PARSE_ERR_RE = re.compile(r"pin count mismatch", re.I)
_NETLIST_READ_RE = re.compile(r"Netlist::read", re.I)


def _read_both_logs(lvs_dir: Path) -> tuple[str, str]:
    """Return (text_6_lvs, text_run_log) for crash-detection; empty string if absent."""
    def _read(p: Path) -> str:
        return p.read_text(encoding='utf-8', errors='ignore') if p.exists() else ''
    return _read(lvs_dir / '6_lvs.log'), _read(lvs_dir / 'lvs_run.log')


def parse_lvs_log(lvs_dir: Path) -> dict:
    """Parse LVS log for status and runtime info.

    Reads both 6_lvs.log and lvs_run.log so that crash signatures present only
    in lvs_run.log (e.g. ``ERROR: Signal number: 11``) are detected correctly.
    Sets info['crash'] = True and info['crash_line'] when a crash is found.
    Sets info['reached_device_extraction'] = True when device-extraction progress
    is logged (indicating an incomplete run, not just a missing log).
    """
    info = {}
    text_main, text_run = _read_both_logs(lvs_dir)
    combined = text_main + "\n" + text_run

    if not combined.strip():
        return info

    # --- crash detection (checked across BOTH logs) ---
    m_crash = _CRASH_RE.search(combined)
    if m_crash:
        info['crash'] = True
        info['crash_line'] = m_crash.group(0)

    # --- device-extraction progress (indicates run started but may not have
    #     produced a verdict) ---
    if _DEVICE_EXTRACT_RE.search(combined):
        info['reached_device_extraction'] = True

    # --- deterministic CDL parse abort (no verdict, not a layout mismatch) ---
    if _CDL_PARSE_ERR_RE.search(combined) and _NETLIST_READ_RE.search(combined):
        info['cdl_parse_error'] = True
        for line in combined.splitlines():
            if _CDL_PARSE_ERR_RE.search(line):
                info['cdl_parse_error_line'] = line.strip()[:300]
                break

    # Use main log (6_lvs.log) preferentially for verdict/timing; fall back to
    # lvs_run.log so the rest of the logic mirrors the original behaviour.
    text = text_main if text_main.strip() else text_run
    lower = text.lower()

    # Determine match status from log — check negative patterns FIRST
    # because "netlists match" is a substring of "netlists don't match"
    if "don't match" in lower or 'do not match' in lower or 'not match' in lower:
        info['log_status'] = 'mismatch'
    elif 'netlists match' in lower or 'lvs clean' in lower or 'circuits match' in lower:
        info['log_status'] = 'match'
    elif 'not supported' in lower:
        info['log_status'] = 'not_supported'

    # Look for elapsed time
    m = re.search(r'(?:real|elapsed|Total time)[:\s]+([\d.]+)', text)
    if m:
        info['elapsed_seconds'] = float(m.group(1))

    # Look for errors
    error_lines = [l.strip() for l in text.splitlines()
                   if 'error' in l.lower() and 'no error' not in l.lower()]
    if error_lines:
        info['errors'] = error_lines[:5]

    # Surface crash line as a prominent error so diagnose_signoff_fix can find it
    if info.get('crash') and info.get('crash_line'):
        errors = info.setdefault('errors', [])
        crash_entry = f"CRASH: {info['crash_line']}"
        if crash_entry not in errors:
            errors.insert(0, crash_entry)

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_lvs.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    lvs_dir = project_root / 'lvs'

    # Honor a fresh skip marker only if no actual LVS log exists. Stale
    # `lvs_result.json` files from a previous run (when the platform had no
    # rules) must NOT override a successful new LVS log/lvsdb.
    skip_file = lvs_dir / 'lvs_result.json'
    log_present = (lvs_dir / '6_lvs.log').exists() or (lvs_dir / 'lvs_run.log').exists()
    if skip_file.exists() and not log_present:
        try:
            skip_data = json.loads(skip_file.read_text(encoding='utf-8'))
            if skip_data.get('status') == 'skipped':
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(skip_data, indent=2), encoding='utf-8')
                print(out_path)
                return
        except Exception:
            pass

    # Netgen path (sky130 production LVS): run_netgen_lvs.sh writes the
    # authoritative verdict to netgen_lvs_result.json and leaves NO KLayout
    # artifacts (no 6_lvs.lvsdb / 6_lvs.log / lvs_run.log). The KLayout parsers
    # below would all return empty and fall through to status='unknown',
    # silently clobbering a clean Netgen result. This is the root cause of the
    # 2026-06-13 LVS-clobber bug: a DRC-fail sky130 design re-ran extract_lvs.py
    # after the fix-loop and lost its already-clean Netgen verdict, mis-recording
    # an LVS-clean design as lvs_unknown (an honesty-invariant violation that
    # poisons run_violations / residual classification). Honor the Netgen result
    # whenever it exists and KLayout produced nothing. Defers to KLayout if its
    # artifacts are present, so nangate45 (KLayout LVS) is byte-identical.
    # Tool-precedence rule: the MOST-RECENTLY-RUN LVS tool is authoritative.
    # The old gate ("netgen wins only if KLayout left NO artifacts") is backwards
    # for the normal sky130 loop, where the fix loop now runs Netgen but stale
    # KLayout artifacts (6_lvs.lvsdb / 6_lvs.log / lvs_run.log) from an earlier
    # run still linger -> extract would defer to the stale (false) KLayout fail.
    # Compare mtimes instead: Netgen wins when its result is at least as fresh as
    # the freshest KLayout artifact. nangate45 (KLayout-only; no netgen_lvs_result.json)
    # is unaffected -> byte-identical. See references/failure-patterns.md "sky130 LVS".
    netgen_file = lvs_dir / 'netgen_lvs_result.json'
    _klayout_arts = [lvs_dir / '6_lvs.lvsdb', lvs_dir / '6_lvs.log',
                     lvs_dir / 'lvs_run.log']
    _klayout_mtime = max((p.stat().st_mtime for p in _klayout_arts if p.exists()),
                         default=0.0)
    netgen_authoritative = (netgen_file.exists()
                            and netgen_file.stat().st_mtime >= _klayout_mtime)
    if netgen_authoritative:
        try:
            ng = json.loads(netgen_file.read_text(encoding='utf-8'))
            ng_status = ng.get('status', 'unknown')
            ng_class = ng.get('mismatch_class') or ''
            out = {
                'status': ng_status,
                'mismatch_count': 0 if ng_status == 'clean' else None,
                'lvsdb': {},
                'log_info': {
                    'tool': 'netgen',
                    'match': ng.get('match'),
                    'report_file': ng.get('report_file'),
                    'log_file': ng.get('log_file'),
                },
                'tool': 'netgen',
            }
            if ng_class:
                out['mismatch_class'] = ng_class
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                encoding='utf-8')
            print(out_path)
            return
        except Exception:
            pass  # malformed netgen JSON -> fall through to KLayout parsing

    lvsdb_result = parse_lvsdb(lvs_dir)
    log_info = parse_lvs_log(lvs_dir)

    lvsdb_exists = (lvs_dir / '6_lvs.lvsdb').exists()

    # Determine overall status
    mismatch_count = lvsdb_result.get('mismatch_count', -1)
    log_status = log_info.get('log_status', '')

    result_reason: str | None = None

    # The COMPARE matched (lvsdb text_match_found, 0 mismatches) but KLayout crashed in a
    # POST-compare step (lvsdb writer net2id assert / 'Internal error ... cleanup'), which
    # emits a spurious "Netlists don't match" -> log_status 'mismatch'. That is a
    # retry-fixable TOOL crash, NOT a real mismatch: classify it 'crash' (the run_lvs.sh
    # LVS_CRASH_RETRIES path produces a clean survivor), never a false 'fail' (2026-06-28
    # PicoRV32_Based_SoC_fifo_basic was a false lvs=fail exactly this way).
    lvsdb_matched = (lvsdb_result.get('raw_status') == 'text_match_found'
                     and mismatch_count == 0)
    if log_status == 'mismatch' and lvsdb_matched and log_info.get('crash'):
        status = 'crash'
        result_reason = 'lvs_writer_crash_after_match'
    elif log_status == 'mismatch':
        status = 'fail'
    elif log_status == 'match' and mismatch_count <= 0:
        status = 'clean'
    elif mismatch_count > 0:
        status = 'fail'
    elif mismatch_count == 0 and log_status == '':
        status = 'clean'  # lvsdb says clean, no log to contradict
    elif log_status == 'not_supported':
        status = 'skipped'
    else:
        # Distinguish crash vs. cdl-parse-error vs. incomplete vs. truly unknown
        if log_info.get('crash'):
            status = 'crash'
            result_reason = 'klayout_cpp_crash'
        elif log_info.get('cdl_parse_error'):
            # KLayout's SPICE reader aborted on a mis-tokenized instance name
            # before any compare — a deterministic CDL-generation/parser issue,
            # not a layout mismatch. Honest, queryable cause under `unknown`.
            status = 'unknown'
            result_reason = 'cdl_parse_error'
        elif log_info.get('reached_device_extraction') and not lvsdb_exists:
            # Run got deep enough to extract devices / write netlist but then
            # died before producing a match/mismatch verdict and no lvsdb file.
            status = 'incomplete'
            result_reason = 'lvs_no_verdict_no_lvsdb'
        else:
            status = 'unknown'

    result = {
        'status': status,
        'mismatch_count': mismatch_count if mismatch_count >= 0 else None,
        'lvsdb': lvsdb_result,
        'log_info': log_info,
    }
    if result_reason is not None:
        result['reason'] = result_reason

    # For a real mismatch with an lvsdb, classify the sub-cause (symmetric-matcher
    # tool residual vs real connectivity vs generic) so the diagnoser can report
    # an honest, specific residual_reason instead of "operator review".
    if status == 'fail' and lvsdb_exists:
        cls = classify_lvs_mismatch(lvs_dir)
        if cls:
            result.update(cls)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
