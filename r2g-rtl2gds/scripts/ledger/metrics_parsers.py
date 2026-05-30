"""Per-step ``key_metrics`` parsers for ``run_ledger.jsonl``.

One parser per ``step`` value defined in TEACHING_POLICY §C. Each parser
takes a mapping ``outputs: {relative_path: absolute_path}`` of the run's
output files and returns the dict to put in ``key_metrics``.

**Honesty contract (critical):**

* If a required output file is missing → return ``{}`` (empty dict).
  An empty ``key_metrics`` is itself a signal to the verifier.
* If a file exists but the expected number cannot be parsed (regex miss,
  malformed report, etc.) → set that specific field to ``None``.
* NEVER substitute zero, dashes, or "guessed" values to make output look
  populated. ``None`` is the truthful answer for "I could not determine this".
  The canonical JSON layer (``canonical.py``) accepts ``None`` and rejects
  NaN/inf, so this is the only safe placeholder.

The registry ``METRICS_PARSERS`` maps the ``step`` string to its parser.
``append_ledger.py`` does a single dictionary lookup; new steps add by
registering here, no changes elsewhere.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger(__name__)

# Type alias: a parser receives {relative_path_str: absolute_Path}
ParserFn = Callable[[Mapping[str, Path]], dict[str, Any]]


# ─── small helpers ───────────────────────────────────────────────────────────

def _find_by_suffix(outputs: Mapping[str, Path], *suffixes: str) -> Optional[Path]:
    """Return the first existing absolute path whose name ends with any suffix."""
    for rel, abs_path in outputs.items():
        if any(rel.endswith(s) for s in suffixes) and abs_path.is_file():
            return abs_path
    return None


_LOG_LIKE_SUFFIXES = (".log", ".rpt", ".txt", ".json", ".lyrdb", ".lvsdb")


def _find_by_keyword(outputs: Mapping[str, Path], keyword: str) -> Optional[Path]:
    """Return the first existing file whose name contains ``keyword``.

    When multiple files match, prefer log-like outputs (.log/.rpt/.txt/.json/
    .lyrdb/.lvsdb) over source/netlist files. This avoids the failure mode
    where (for example) a synthesis run produces both ``synth.log`` AND
    ``demo.synth.v`` and the parser picks up the netlist instead of the log.
    """
    log_like: Optional[Path] = None
    fallback: Optional[Path] = None
    for rel, abs_path in outputs.items():
        name = Path(rel).name
        if keyword not in name or not abs_path.is_file():
            continue
        if name.endswith(_LOG_LIKE_SUFFIXES):
            if log_like is None:
                log_like = abs_path
        elif fallback is None:
            fallback = abs_path
    return log_like if log_like is not None else fallback


def _read_text(p: Path, max_bytes: int = 32 * 1024 * 1024) -> Optional[str]:
    """Read a text file with a sanity cap. Returns None on IO error."""
    try:
        if p.stat().st_size > max_bytes:
            log.warning("file too large to parse: %s", p)
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("cannot read %s: %s", p, e)
        return None


def _grep_float(text: str, pattern: str) -> Optional[float]:
    """Search ``text`` for ``pattern`` (must capture one numeric group)."""
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, IndexError):
        return None


def _grep_int(text: str, pattern: str) -> Optional[int]:
    v = _grep_float(text, pattern)
    return int(v) if v is not None else None


# ─── parsers (one per step) ──────────────────────────────────────────────────

def parse_lint(outputs: Mapping[str, Path]) -> dict[str, Any]:
    log_path = _find_by_keyword(outputs, "lint")
    if log_path is None:
        return {}
    text = _read_text(log_path) or ""
    # Verilator-style: "%Warning..." / "%Error..." line markers
    warn = len(re.findall(r"^%Warning", text, flags=re.MULTILINE))
    err = len(re.findall(r"^%Error", text, flags=re.MULTILINE))
    # Fallback: generic "Warning:" / "Error:" counts if no Verilator markers
    if warn == 0 and err == 0:
        warn = len(re.findall(r"\bWarning\b[: ]", text))
        err = len(re.findall(r"\bError\b[: ]", text))
    return {"warning_count": warn, "error_count": err}


def parse_simulation(outputs: Mapping[str, Path]) -> dict[str, Any]:
    log_path = _find_by_keyword(outputs, "sim")
    if log_path is None:
        return {}
    text = _read_text(log_path) or ""
    # Conservative pass detection: an explicit "TEST PASSED" or "PASS" line
    # from the testbench. Absence is reported as None, not False.
    pass_markers = re.search(r"\b(TEST\s+PASSED|TESTS?\s+PASSED|^PASS\b)\b",
                             text, flags=re.MULTILINE | re.IGNORECASE)
    fail_markers = re.search(r"\b(TEST\s+FAILED|FAIL(?:URE|ED)?)\b",
                             text, flags=re.MULTILINE | re.IGNORECASE)
    if pass_markers and not fail_markers:
        tb_pass: Optional[bool] = True
    elif fail_markers:
        tb_pass = False
    else:
        tb_pass = None
    sim_cycles = _grep_int(text, r"(?:cycles|ticks)[:\s=]+(\d+)")
    return {"tb_pass": tb_pass, "sim_cycles": sim_cycles}


def parse_synthesis(outputs: Mapping[str, Path]) -> dict[str, Any]:
    log_path = _find_by_keyword(outputs, "synth")
    if log_path is None:
        return {}
    text = _read_text(log_path) or ""
    # Yosys "stat" report block; supports both ABC-after and plain Yosys.
    cell_count = _grep_int(text, r"Number of cells:\s+(\d+)")
    # Area (um^2) appears under "Chip area for module" in Yosys stat -liberty.
    area_um2 = _grep_float(text, r"Chip area for (?:module|top module).*?:\s*([\d.]+)")
    # Top module appears as "Top module:  \\<name>" or "Top: <name>"
    top_match = re.search(r"^(?:Top module|Top)\s*[:=]\s*\\?(\S+)",
                          text, flags=re.MULTILINE)
    top_module = top_match.group(1) if top_match else None
    return {
        "cell_count": cell_count,
        "area_um2": area_um2,
        "top_module": top_module,
    }


def parse_orfs_backend(outputs: Mapping[str, Path]) -> dict[str, Any]:
    # OpenROAD finish report: 6_report.json / 6_final.rpt depending on ORFS ver.
    # Strategy: try the structured .json first; fall back to .rpt regex.
    rpt = _find_by_suffix(outputs, "6_report.json", "finish_report.json")
    if rpt:
        try:
            import json
            data = json.loads(_read_text(rpt) or "{}")
            return {
                "wns_ns": data.get("finish__timing__wns__worst") or
                          data.get("wns__ns"),
                "tns_ns": data.get("finish__timing__tns__total") or
                          data.get("tns__ns"),
                "instance_count": data.get("finish__design__instance__count") or
                                  data.get("instance_count"),
                "core_area_um2": data.get("finish__design__core__area") or
                                 data.get("core_area_um2"),
                "die_area_um2": data.get("finish__design__die__area") or
                                data.get("die_area_um2"),
            }
        except (ValueError, OSError):
            pass  # fall through to regex
    rpt = _find_by_suffix(outputs, ".rpt") or _find_by_keyword(outputs, "finish")
    if rpt is None:
        return {}
    text = _read_text(rpt) or ""
    return {
        "wns_ns": _grep_float(text, r"wns[^\d\-]*([-\d.]+)\s*ns"),
        "tns_ns": _grep_float(text, r"tns[^\d\-]*([-\d.]+)\s*ns"),
        "instance_count": _grep_int(text, r"(?:instance|inst)\s*count[:\s]+(\d+)"),
        "core_area_um2": _grep_float(text, r"core\s*area[:\s]+([\d.]+)"),
        "die_area_um2": _grep_float(text, r"die\s*area[:\s]+([\d.]+)"),
    }


def parse_timing_check(outputs: Mapping[str, Path]) -> dict[str, Any]:
    rpt = _find_by_keyword(outputs, "timing")
    if rpt is None:
        return {}
    text = _read_text(rpt) or ""
    # r2g check_timing.py classifies into one of these tiers.
    tier_match = re.search(
        r"\btier[:\s]+(minor|moderate|severe|unconstrained|clean)\b",
        text, flags=re.IGNORECASE,
    )
    tier = tier_match.group(1).lower() if tier_match else None
    return {
        "tier": tier,
        "wns_ns": _grep_float(text, r"wns[^\d\-]*([-\d.]+)\s*ns"),
        "tns_ns": _grep_float(text, r"tns[^\d\-]*([-\d.]+)\s*ns"),
    }


def parse_drc_klayout(outputs: Mapping[str, Path]) -> dict[str, Any]:
    # KLayout DRC writes a .lyrdb (XML) or .rpt with a count.
    rpt = _find_by_suffix(outputs, ".lyrdb", ".rpt", ".drc.log")
    if rpt is None:
        return {}
    text = _read_text(rpt) or ""
    # lyrdb: <item> tags per violation; .rpt: a "Total violations: N" line.
    v_total = _grep_int(text, r"Total\s+violations?[:\s]+(\d+)")
    if v_total is None:
        # Fallback: count <item> tags in lyrdb XML.
        count = len(re.findall(r"<item\b", text))
        v_total = count if count > 0 else None
    return {"violation_count": v_total}


def parse_lvs_klayout(outputs: Mapping[str, Path]) -> dict[str, Any]:
    rpt = _find_by_suffix(outputs, ".lvsdb", ".rpt", ".lvs.log")
    if rpt is None:
        return {}
    text = _read_text(rpt) or ""
    if re.search(r"\bLVS\s+(?:CLEAN|PASS|MATCHED)\b", text, re.IGNORECASE):
        status: Optional[str] = "PASS"
    elif re.search(r"\bLVS\s+(?:FAIL|MISMATCH)", text, re.IGNORECASE):
        status = "FAIL"
    elif re.search(r"\b(?:BLOCKED|MISSING\s+RULES)\b", text, re.IGNORECASE):
        status = "BLOCKED"
    else:
        status = None
    mismatched = _grep_int(text, r"(\d+)\s+mismatched?\s+nets?")
    return {"status": status, "mismatched_nets": mismatched}


def parse_rcx_openrcx(outputs: Mapping[str, Path]) -> dict[str, Any]:
    spef = _find_by_suffix(outputs, ".spef", ".spef.gz")
    if spef is None:
        return {}
    # SPEF body uses *RES / *CAP / *D_NET sections; count is a coarse proxy
    # for "did we actually extract parasitics".
    text = _read_text(spef) or ""
    parasitic_count = len(re.findall(r"^\*D_NET\b", text, flags=re.MULTILINE))
    # Hash of SPEF is captured separately by outputs[…]; here we just record
    # the path for cross-reference. autograder will verify the hash.
    return {
        "spef_path": str(spef.name),
        "parasitic_count": parasitic_count if parasitic_count > 0 else None,
    }


def _parse_label_csv(outputs: Mapping[str, Path], suffix: str) -> dict[str, Any]:
    csv = _find_by_suffix(outputs, suffix)
    if csv is None:
        return {}
    try:
        with csv.open("r", encoding="utf-8") as f:
            # Subtract 1 for the header line; report 0 explicitly if file is
            # header-only (empty result is still a real result).
            line_count = sum(1 for _ in f)
        row_count = max(0, line_count - 1)
    except OSError:
        return {"row_count": None}
    return {"row_count": row_count}


def parse_label_wirelength(outputs: Mapping[str, Path]) -> dict[str, Any]:
    return _parse_label_csv(outputs, "wirelength.csv")


def parse_label_congestion(outputs: Mapping[str, Path]) -> dict[str, Any]:
    return _parse_label_csv(outputs, "cell_congestion.csv")


def parse_label_timing(outputs: Mapping[str, Path]) -> dict[str, Any]:
    return _parse_label_csv(outputs, "timing_features.csv")


def parse_label_irdrop(outputs: Mapping[str, Path]) -> dict[str, Any]:
    return _parse_label_csv(outputs, "ir_drop.csv")


# ─── registry ────────────────────────────────────────────────────────────────

METRICS_PARSERS: dict[str, ParserFn] = {
    "lint":            parse_lint,
    "simulation":      parse_simulation,
    "synthesis":       parse_synthesis,
    "orfs_backend":    parse_orfs_backend,
    "timing_check":    parse_timing_check,
    "drc_klayout":     parse_drc_klayout,
    "lvs_klayout":     parse_lvs_klayout,
    "rcx_openrcx":     parse_rcx_openrcx,
    "label_wirelength": parse_label_wirelength,
    "label_congestion": parse_label_congestion,
    "label_timing":     parse_label_timing,
    "label_irdrop":     parse_label_irdrop,
}


def get_parser(step: str) -> ParserFn:
    """Return the parser for ``step``, or raise KeyError with a helpful message.

    Unknown steps must NOT silently produce empty metrics — that would be a
    schema drift and the autograder would later see records with mysteriously
    blank ``key_metrics``. Fail loud and early instead.
    """
    if step not in METRICS_PARSERS:
        raise KeyError(
            f"unknown step {step!r}; register a parser in metrics_parsers.py. "
            f"Known steps: {sorted(METRICS_PARSERS)}"
        )
    return METRICS_PARSERS[step]
