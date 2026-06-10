#!/usr/bin/env python3
"""Ingest one design_cases/<project> directory into knowledge/knowledge.sqlite.

Usage:
  ingest_run.py <project-dir>
  ingest_run.py <project-dir> --db <path>

Reads the structured JSON artifacts the flow already produces:
  constraints/config.mk
  reports/ppa.json
  reports/timing_check.json
  reports/drc.json
  reports/lvs.json
  reports/rcx.json
  reports/diagnosis.json
  backend/stage_log.jsonl

Nothing here parses raw ORFS logs — if an artifact is missing, the
corresponding column is left NULL. Idempotent: re-ingesting the same
completed run produces the same run_id.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import knowledge_db
import symptom


_CONFIG_LINE_RE = re.compile(r"(?:export\s+)?(\w+)\s*=\s*(.*)")


def _parse_config_mk(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _CONFIG_LINE_RE.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stage_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# Maps every accepted verdict string to the canonical fix verdict vocabulary
# (cleared|win|no_change|regression|inconclusive). Two origins feed the ingester:
#   1. fix_signoff.sh legacy strings: applied / no_improvement (cleared is already canonical).
#   2. check_timing.py --journal canonical strings: win / no_change / regression / cleared.
# Canonical strings must pass through idempotently — before this they fell through to
# 'inconclusive', silently dropping the learning signal from timing-journal episodes.
_VERDICT_MAP = {
    "cleared": "cleared",
    "applied": "win",
    "no_improvement": "no_change",
    "win": "win",
    "no_change": "no_change",
    "regression": "regression",
    "inconclusive": "inconclusive",
}


def _normalize_verdict(raw: str | None, before: Any, after: Any) -> str:
    if raw in _VERDICT_MAP:
        v = _VERDICT_MAP[raw]
        # 'applied' with a worse count is a regression, not a win.
        if v == "win" and before is not None and after is not None and after > before:
            return "regression"
        if v == "win" and before is not None and after is not None and after == before:
            return "no_change"
        return v
    return "inconclusive"   # stop_* / apply_failed / rerun_failed_* / unknown


def _explicit_family(name: str, families: dict[str, Any]) -> str | None:
    """Family from an EXPLICIT families.json mapping or pattern, or None if only
    the generic split-on-underscore fallback would apply."""
    if not name:
        return None
    if name in families.get("mappings", {}):
        return families["mappings"][name]
    for entry in families.get("patterns", []):
        if re.search(entry["regex"], name, re.IGNORECASE):
            return entry["family"]
    return None


def _project_family(project: Path, design_name: str, families: dict[str, Any]) -> str:
    """Infer the design family consistently with backfill_fix_events.

    A curated DESIGN_NAME mapping/pattern wins (e.g. ChipTop->boom_chiptop,
    ^aes->aes_xcrypt). Otherwise infer from the PROJECT-DIR basename, which carries
    the source-repo prefix that config.mk's DESIGN_NAME drops — so harvested designs
    group the same way backfill grouped them (e.g. dir wb2axip_axi2axilite ->
    'wb2axip' not DESIGN_NAME 'axi2axilite'->'axi2axilite'; iccad2015_unit18_in1 ->
    'iccad2015' not DESIGN_NAME 'test'). Keeps live ingest and backfill in one
    family namespace so fix_recipes aggregate correctly."""
    return _explicit_family(design_name, families) or knowledge_db.infer_family(
        project.name, families)


def _read_fix_log(project: Path) -> list[dict[str, Any]]:
    return _read_stage_log(project / "reports" / "fix_log.jsonl")


# Size bands match suggest_config.recommend (tiny<100, small<5000, medium<50000).
def _size_class(cell_count: int | None) -> str:
    if not cell_count:
        return "unknown"
    if cell_count < 100:
        return "tiny"
    if cell_count < 5000:
        return "small"
    if cell_count < 50000:
        return "medium"
    return "large"


# Keep keyword sets in sync with suggest_config.detect_design_type (the
# canonical classifier; this is the ingest-side mirror for stored runs).
_BUS_KW = ("crossbar", "arbiter", "interconnect", "wb_conmax", "axi_", "ahb_")
_CRYPTO_KW = ("aes", "sha", "des_", "cipher", "encrypt", "sbox")


def _design_type(project: Path, cfg: dict[str, str]) -> str:
    blob = ""
    rtl_dir = project / "rtl"
    if rtl_dir.is_dir():
        for f in sorted(rtl_dir.glob("*.v"))[:50]:
            try:
                blob += f.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                pass
    if any(k in blob for k in _BUS_KW):
        return "bus_heavy"
    if any(k in blob for k in _CRYPTO_KW):
        return "crypto"
    if "sram" in blob or cfg.get("ADDITIONAL_LEFS"):
        return "macro_heavy"
    return "logic"


def _heuristics_generation() -> int | None:
    import os
    hp = Path(os.environ.get("R2G_HEURISTICS_PATH",
              knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"))
    data = _read_json(hp) or {}
    return data.get("generation")


def _journal_report_digests(project: Path) -> None:
    """One log_summaries digest row per report generated by ORFS / EDA tools:
    the skill's extracted reports/*.json plus any JSON reports in the newest
    backend RUN_* dir (spec rev 3, decision 10). Never breaks ingest."""
    try:
        import os
        import journal_db
        import summarize_log
        jpath = os.environ.get("R2G_JOURNAL_DB", journal_db.DEFAULT_JOURNAL_PATH)
        conn = journal_db.connect(jpath)
        journal_db.ensure_schema(conn)
        proj_str = str(project.resolve())
        conn.execute("DELETE FROM log_summaries WHERE project_path=? AND "
                     "tool='report'", (proj_str,))
        candidates = sorted((project / "reports").glob("*.json"))
        backend = project / "backend"
        if backend.is_dir():
            run_dirs = sorted(
                (d for d in backend.iterdir()
                 if d.is_dir() and d.name.startswith("RUN_")),
                key=lambda d: d.stat().st_mtime, reverse=True)
            if run_dirs:
                candidates += sorted(run_dirs[0].glob("*.json"))
        for f in candidates:
            rep = _read_json(f)
            if rep is None:
                continue
            s = summarize_log.summarize_report(rep, kind=f.stem)
            journal_db.append_log_summary(
                conn, project_path=proj_str, stage=f.stem, tool="report",
                source_path=str(f), status=s["status"], metrics=s["metrics"],
                digest=s["digest"])
        conn.close()
    except Exception as exc:
        print(f"WARNING: report digest sweep skipped: {exc}", file=sys.stderr)


def _upsert_symptom(conn: sqlite3.Connection, sig: dict, sid: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO symptoms "
        "(symptom_id, check_type, class, predicates_json, symptom_schema_version, first_seen) "
        "VALUES (?,?,?,?,?,?)",
        (sid, sig.get("check"), sig.get("class"),
         json.dumps(sig.get("predicates") or {}, sort_keys=True),
         symptom.SYMPTOM_SCHEMA_VERSION,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"))


def _ingest_fix_events(conn: sqlite3.Connection, project: Path,
                       design_name: str, design_family: str, platform: str) -> int:
    """Read reports/fix_log.jsonl into fix_events (idempotent via UNIQUE).

    On re-ingest, the ON CONFLICT clause backfills the enrichment columns
    (config_delta_json, env_flags_json, symptom_id, signature_json) WITHOUT
    clobbering provenance or other stable fields.
    """
    rows = _read_fix_log(project)
    n = 0
    for r in rows:
        sid = r.get("fix_session_id")
        if not sid:
            continue
        before = _to_float(r.get("before"))
        after = _to_float(r.get("after"))
        sig, symptom_id_ = symptom.from_fix_log_row(r)
        _upsert_symptom(conn, sig, symptom_id_)
        conn.execute(
            "INSERT INTO fix_events "
            "(fix_session_id, project_path, design_name, design_family, platform, "
            " check_type, violation_class, iter, strategy, from_stage, "
            " before_count, after_count, before_categories_json, after_categories_json, "
            " before_status, after_status, verdict, cumulative_config_json, "
            " config_delta_json, env_flags_json, symptom_id, signature_json, ts, provenance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fix_session_id, iter, strategy) DO UPDATE SET "
            "  config_delta_json=excluded.config_delta_json, "
            "  env_flags_json=excluded.env_flags_json, "
            "  symptom_id=excluded.symptom_id, "
            "  signature_json=excluded.signature_json",
            (sid, str(project.resolve()), design_name, design_family, platform,
             r.get("check"), r.get("violation_class"), _to_int(r.get("iter")),
             r.get("strategy"), r.get("from_stage"), before, after,
             r.get("before_categories"), r.get("after_categories"),
             r.get("before_status"), r.get("after_status"),
             _normalize_verdict(r.get("verdict"), before, after),
             r.get("cumulative_config"), r.get("config_delta"), r.get("env_flags"),
             symptom_id_, json.dumps(sig, sort_keys=True),
             r.get("ts"), "live"))
        n += 1
    return n


def _write_run_violations(conn: sqlite3.Connection, run_id: str,
                          design_family: str, platform: str,
                          drc: dict[str, Any], lvs: dict[str, Any],
                          tcheck: dict[str, Any], wns: Any) -> None:
    # Per-run symptom: prefer the failing check (LVS fail -> mismatch_class symptom,
    # else DRC -> dominant category, else timing tier). Family is NOT part of it.
    if lvs.get("status") == "fail":
        check, vclass, report = "lvs", lvs.get("mismatch_class"), lvs
    elif drc.get("status") == "fail":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        check, report = "drc", drc
    else:
        check, vclass, report = "timing", tcheck.get("tier"), {}
    sig = symptom.canonical_signature(check, vclass, symptom.predicates_for(check, report))
    sid = symptom.symptom_id(sig)
    _upsert_symptom(conn, sig, sid)
    conn.execute(
        "INSERT OR REPLACE INTO run_violations "
        "(run_id, design_family, platform, drc_status, drc_categories_json, "
        " lvs_status, lvs_mismatch_class, timing_tier, wns_ns, symptom_id, "
        " signature_json, snapshot_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, design_family, platform, drc.get("status"),
         json.dumps(drc.get("categories") or {}, sort_keys=True),
         lvs.get("status"), lvs.get("mismatch_class"), tcheck.get("tier"), wns,
         sid, json.dumps(sig, sort_keys=True),
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"))


# orfs_status is intentionally a FAITHFUL record of backend/stage_log.jsonl:
# it returns 'pass' only when all six stages appear there, and does NOT infer
# completion from signoff (a clean GDS implies finish ran, but we don't
# back-fill the stage log). Signoff-based "did this run reach a signed-off
# layout" learning is handled separately by knowledge_db.is_success in the
# learner — keep this function a pure stage-log mirror; do not change it to
# read drc/lvs/rcx.
def _derive_orfs_status(stages: list[dict[str, Any]]) -> tuple[str, str | None]:
    if not stages:
        return ("unknown", None)
    saw_fail = False
    fail_stage = None
    last_stage_name = None
    stage_names_done = {s.get("stage") for s in stages if s.get("status") == "pass"}
    for s in stages:
        if s.get("status") not in ("pass", "fail"):
            continue
        last_stage_name = s.get("stage")
        if s.get("status") == "fail" and not saw_fail:
            saw_fail = True
            fail_stage = s.get("stage")
    if saw_fail:
        return ("fail", fail_stage)
    required = ["synth", "floorplan", "place", "cts", "route", "finish"]
    if all(name in stage_names_done for name in required):
        return ("pass", None)
    return ("partial", last_stage_name)


def _compute_run_id(project: Path, ppa_path: Path) -> str:
    marker = str(ppa_path.stat().st_mtime_ns) if ppa_path.exists() else ""
    h = hashlib.sha1()
    h.update(str(project.resolve()).encode("utf-8"))
    h.update(b":")
    h.update(marker.encode("utf-8"))
    return h.hexdigest()


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _coerce_bool_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if s in ("1", "true", "TRUE", "True", "yes"):
        return 1
    if s in ("0", "false", "FALSE", "False", "no", ""):
        return 0
    return None


def _record_lineage(conn: sqlite3.Connection, run_id: str,
                    design_name: str, platform: str,
                    cfg: dict[str, str], orfs_status: str,
                    outcome_fields: dict) -> None:
    """If a previous run exists for this design/platform, record the config diff."""
    prev = conn.execute(
        "SELECT run_id, extra_config_json, core_utilization, "
        "place_density_lb_addon, synth_hierarchical, abc_area, die_area, "
        "clock_period_ns "
        "FROM runs "
        "WHERE design_name = ? AND platform = ? AND run_id != ? "
        "ORDER BY ingested_at DESC LIMIT 1",
        (design_name, platform, run_id),
    ).fetchone()
    if prev is None:
        return

    # Keys that are design identity, not tuning parameters — exclude from diff
    _IDENTITY_KEYS = {"DESIGN_NAME", "PLATFORM", "VERILOG_FILES", "SDC_FILE"}

    prev_run_id = prev[0]
    # Reconstruct previous config dict from stored columns
    prev_cfg: dict[str, str] = {}
    if prev[1]:  # extra_config_json
        try:
            extra = json.loads(prev[1])
            prev_cfg.update({k: v for k, v in extra.items()
                             if k not in _IDENTITY_KEYS})
        except (json.JSONDecodeError, TypeError):
            pass
    col_map = {
        "CORE_UTILIZATION": prev[2], "PLACE_DENSITY_LB_ADDON": prev[3],
        "SYNTH_HIERARCHICAL": prev[4], "ABC_AREA": prev[5],
        "DIE_AREA": prev[6], "CLOCK_PERIOD": prev[7],
    }
    for k, v in col_map.items():
        if v is not None:
            # Normalize float DB values so "30.0" matches raw config string "30"
            s = str(v)
            if s.endswith(".0") and s[:-2].lstrip("-").isdigit():
                s = s[:-2]
            prev_cfg[k] = s

    # Normalize current config values to strings for comparison.
    # Exclude identity keys — not tuning parameters, not stored in prev_cfg.
    cur_cfg = {k: str(v).strip() for k, v in cfg.items()
               if v and k not in _IDENTITY_KEYS}

    # Canonicalize numeric strings so "0.20" == "0.2" and "30" == "30.0".
    # This prevents spurious diffs caused by float DB round-trips.
    def _canon(s: str) -> str:
        try:
            f = float(s)
            # If it's a whole number, use integer string representation
            if f == int(f):
                return str(int(f))
            # Otherwise use repr to avoid trailing zeros (0.20 → 0.2)
            return str(f)
        except (ValueError, OverflowError):
            return s

    prev_cfg = {k: _canon(v) for k, v in prev_cfg.items()}
    cur_cfg = {k: _canon(v) for k, v in cur_cfg.items()}

    diff = knowledge_db.diff_config_rows(prev_cfg, cur_cfg)
    if not diff["changed"] and not diff["added"] and not diff["removed"]:
        return

    current_outcome = json.dumps({
        "is_success": knowledge_db.is_success({
            "orfs_status": orfs_status,
            "drc_status": outcome_fields.get("drc_status"),
            "lvs_status": outcome_fields.get("lvs_status"),
            "rcx_status": outcome_fields.get("rcx_status"),
            "lvs_mismatch_class": outcome_fields.get("lvs_mismatch_class"),
        }),
        "orfs_status": orfs_status,
        "wns_ns": outcome_fields.get("wns_ns"),
        "drc_violations": outcome_fields.get("drc_violations"),
        "total_elapsed_s": outcome_fields.get("total_elapsed_s"),
    }, sort_keys=True)

    conn.execute(
        "INSERT INTO config_lineage "
        "(design_name, platform, current_run_id, previous_run_id, "
        " diff_json, current_outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (design_name, platform, run_id, prev_run_id,
         json.dumps(diff, sort_keys=True), current_outcome,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )


def ingest(project: Path,
           conn: sqlite3.Connection,
           families_path: Path | None = None) -> str:
    project = Path(project)
    if not project.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project}")

    families_path = Path(families_path) if families_path else knowledge_db.DEFAULT_FAMILIES_PATH
    families = knowledge_db.load_families(families_path)

    cfg = _parse_config_mk(project / "constraints" / "config.mk")
    design_name = cfg.get("DESIGN_NAME", "unknown")
    design_family = _project_family(project, design_name, families)
    platform = cfg.get("PLATFORM", "nangate45")

    ppa = _read_json(project / "reports" / "ppa.json") or {}
    summary = ppa.get("summary", {}) if isinstance(ppa, dict) else {}
    timing = summary.get("timing", {}) if isinstance(summary, dict) else {}
    power = summary.get("power", {}) if isinstance(summary, dict) else {}
    area = summary.get("area", {}) if isinstance(summary, dict) else {}
    geometry = ppa.get("geometry", {}) if isinstance(ppa, dict) else {}

    drc = _read_json(project / "reports" / "drc.json") or {}
    lvs = _read_json(project / "reports" / "lvs.json") or {}
    rcx = _read_json(project / "reports" / "rcx.json") or {}
    tcheck = _read_json(project / "reports" / "timing_check.json") or {}
    diag = _read_json(project / "reports" / "diagnosis.json") or {}
    # stage_log.jsonl lives inside backend/RUN_<timestamp>/.  Find the
    # most-recently-modified one, falling back to the legacy flat path.
    stage_log_path = project / "backend" / "stage_log.jsonl"
    run_dirs = sorted(
        (d for d in (project / "backend").iterdir()
         if d.is_dir() and d.name.startswith("RUN_")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    ) if (project / "backend").is_dir() else []
    for rd in run_dirs:
        candidate = rd / "stage_log.jsonl"
        if candidate.exists():
            stage_log_path = candidate
            break
    stage_log = _read_stage_log(stage_log_path)

    orfs_status, fail_stage = _derive_orfs_status(stage_log)
    total_elapsed = sum(_to_float(s.get("elapsed_s")) or 0.0 for s in stage_log) or None

    # Cell count: prefer geometry.instance_count (authoritative, from 6_report.json),
    # fall back to geometry.stdcell_count when instance_count is absent in partial runs.
    cell_count = _to_int(geometry.get("instance_count"))
    if cell_count is None:
        cell_count = _to_int(geometry.get("stdcell_count"))

    # Area: geometry.die_area_um2 is authoritative; area.design_area_um2 is a
    # placer-stage estimate used as fallback.
    area_um2 = _to_float(geometry.get("die_area_um2"))
    if area_um2 is None:
        area_um2 = _to_float(area.get("design_area_um2"))

    # Power: extract_ppa.py stores total_power_w in Watts; convert to mW.
    total_power_w = _to_float(power.get("total_power_w"))
    power_mw = total_power_w * 1000.0 if total_power_w is not None else None

    ppa_path = project / "reports" / "ppa.json"
    run_id = _compute_run_id(project, ppa_path)

    design_class = f"{_design_type(project, cfg)}/{_size_class(cell_count)}"
    prior = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE design_name=? AND platform=? AND run_id!=?",
        (design_name, platform, run_id)).fetchone()[0]
    is_clean = (drc.get("status") in ("clean", "clean_beol")
                and lvs.get("status") in ("clean", "skipped", None))
    fix_rows = _read_fix_log(project)
    cleared = [r for r in fix_rows if r.get("verdict") == "cleared"]
    fix_iters_to_clean = max((_to_int(r.get("iter")) or 0 for r in cleared),
                             default=None) if cleared else None

    row = {
        "run_id":            run_id,
        "project_path":      str(project.resolve()),
        "design_name":       design_name,
        "design_family":     design_family,
        "platform":          platform,
        "ingested_at":       _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",

        "core_utilization":       _to_float(cfg.get("CORE_UTILIZATION")),
        "place_density_lb_addon": _to_float(cfg.get("PLACE_DENSITY_LB_ADDON")),
        "synth_hierarchical":     _coerce_bool_int(cfg.get("SYNTH_HIERARCHICAL")),
        "abc_area":               _coerce_bool_int(cfg.get("ABC_AREA")),
        "die_area":               cfg.get("DIE_AREA"),
        "clock_period_ns":        _to_float(cfg.get("CLOCK_PERIOD")),
        "extra_config_json":      json.dumps({
            k: v for k, v in cfg.items()
            if k not in {
                "DESIGN_NAME", "PLATFORM", "CORE_UTILIZATION",
                "PLACE_DENSITY_LB_ADDON", "SYNTH_HIERARCHICAL", "ABC_AREA",
                "DIE_AREA", "CLOCK_PERIOD", "EVAL_ARM",
            }
        }, sort_keys=True),

        "orfs_status":     orfs_status,
        "orfs_fail_stage": fail_stage,
        "wns_ns":          _to_float(timing.get("setup_wns")),
        "tns_ns":          _to_float(timing.get("setup_tns")),
        "timing_tier":     tcheck.get("tier"),
        "cell_count":      cell_count,
        "area_um2":        area_um2,
        "power_mw":        power_mw,
        "drc_status":      drc.get("status"),          # clean | fail | unknown
        "drc_violations":  _to_int(drc.get("total_violations")),
        "lvs_status":      lvs.get("status"),          # clean | fail | skipped | crash | incomplete | unknown
        "lvs_mismatch_class": lvs.get("mismatch_class"),  # symmetric_matcher | real_connectivity | generic (fail only)
        "rcx_status":      rcx.get("status"),          # complete | empty | no_spef | skipped
        "eval_arm":        cfg.get("EVAL_ARM"),         # naive | learned | None (payoff A/B harness)
        "design_class":          design_class,
        "heuristics_generation": _heuristics_generation(),
        "first_attempt_clean":   (1 if is_clean else 0) if prior == 0 else 0,
        "fix_iters_to_clean":    fix_iters_to_clean,
        "wall_s_to_clean":       total_elapsed if is_clean else None,

        "total_elapsed_s":  total_elapsed,
        "stage_times_json": json.dumps(stage_log, sort_keys=True),
    }

    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
        row,
    )

    # Rebuild failure events for this run (idempotent).
    conn.execute("DELETE FROM failure_events WHERE run_id = ?", (run_id,))
    for issue in (diag.get("issues") or []):
        sig = (issue.get("kind") or "").strip()
        if not sig:
            continue
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, issue.get("stage"), sig, issue.get("summary")),
        )
    if orfs_status == "fail" and fail_stage:
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, fail_stage, f"orfs-fail-{fail_stage}", None),
        )
    _ingest_fix_events(conn, project, design_name, design_family, platform)
    _write_run_violations(conn, run_id, design_family, platform, drc, lvs, tcheck,
                          _to_float(timing.get("setup_wns")))
    _record_lineage(conn, run_id, design_name, platform, cfg, orfs_status,
                    outcome_fields={
                        "drc_status": drc.get("status"),
                        "lvs_status": lvs.get("status"),
                        "rcx_status": rcx.get("status"),
                        "lvs_mismatch_class": lvs.get("mismatch_class"),
                        "wns_ns": _to_float(timing.get("setup_wns")),
                        "drc_violations": _to_int(drc.get("total_violations")),
                        "total_elapsed_s": total_elapsed,
                    })
    _journal_report_digests(project)
    try:
        import os
        import journal_db
        jpath = os.environ.get("R2G_JOURNAL_DB", journal_db.DEFAULT_JOURNAL_PATH)
        if Path(jpath).exists():
            jc = journal_db.connect(jpath)
            journal_db.backfill_run_id(jc, project_path=str(project.resolve()),
                                       run_id=run_id)
            jc.close()
    except Exception as exc:
        print(f"WARNING: journal run_id backfill skipped: {exc}", file=sys.stderr)
    conn.commit()
    return run_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path, help="Path to design_cases/<project> directory")
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH,
                   help="SQLite database path (default: knowledge/knowledge.sqlite)")
    p.add_argument("--schema", type=Path, default=knowledge_db.DEFAULT_SCHEMA_PATH,
                   help="Schema SQL path")
    p.add_argument("--families", type=Path, default=knowledge_db.DEFAULT_FAMILIES_PATH,
                   help="families.json path")
    args = p.parse_args()

    conn = knowledge_db.connect(args.db)
    knowledge_db.ensure_schema(conn, schema_path=args.schema)
    run_id = ingest(args.project, conn, families_path=args.families)
    # Warn loudly if the run is about to be classified 'unknown' because
    # stage_log.jsonl is missing — this silently excludes runs from learning.
    status_row = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id = ?", (run_id,),
    ).fetchone()
    if status_row and status_row[0] == "unknown":
        print(
            f"WARNING: no backend/stage_log.jsonl under {args.project}; "
            "orfs_status='unknown'. This run will NOT contribute to "
            "learn_heuristics.py. Re-run via run_orfs.sh to emit stage_log.jsonl.",
            file=sys.stderr,
        )
    conn.close()
    print(f"Ingested run_id={run_id} from {args.project}")
    # Autonomous post-ingest: re-derive Tier-2/Tier-3 and enforce the size policy
    # (env-gated; a failure here must never break the flow ingest above).
    import os
    if os.environ.get("R2G_FIX_AUTOLEARN", "1") == "1":
        try:
            import fix_log_manager
            fix_log_manager.manage(args.db)
        except Exception as exc:
            print(f"WARNING: fix_log_manager.manage skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
