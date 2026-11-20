#!/usr/bin/env python3
"""Learned repair recipes: accumulate the fixes an LLM fallback found after R2G's own recipes
failed, and reuse them the next time R2G meets the same symptom (2026-11-08).

The loop this closes
--------------------
R2G stops on a design (no strategy for the symptom, or every strategy failed) -> the LLM
fallback starts from R2G's final project and reaches a verified DRC-clean + LVS-matched layout ->
``distill()`` turns the DIFFERENCE between R2G's final project and the LLM's final project into a
recipe keyed by R2G's own symptom signature (knowledge.symptom) -> ``record()`` stores it in the
knowledge DB as a ``candidate`` -> the next time R2G stops on that symptom, ``inject()`` offers the
recipe as an authorized candidate attempt BEFORE the run escalates to the LLM again.

What a recipe may contain (everything else is REJECTED, with the reason recorded)
  * config.mk knob values (numeric / string), checked by the same repair-action policy as every
    built-in strategy at apply time;
  * Tcl hook scripts the LLM wrote (stored by content, materialized under <project>/hooks/learned/);
  * a changed platform-override file (PDN / tapcell Tcl) -- stored by content like a hook.
  Rejected: any RTL edit (design-specific, and only valid with a formal-equivalence proof), a changed
  source-file set or top module, an SDC change (the clock and constraints are frozen), a platform
  change, a path that cannot be resolved to content, and an empty delta (a pure path re-plumbing).

Selective admission (not every successful fallback becomes a recipe)
  A distilled fix is OFFERED by R2G only once it is
  * reasonable  -- every edited variable is a known ORFS / R2G flow variable, no edit switches off a
                   repair or a check (SKIP_*, DISABLE_*, ENABLE_*=0, blanked *_VOLTAGES), at most
                   MAX_EDITS edits after the read-only probes are dropped;
  * generalizable -- no design-specific die/core coordinates, and no hook that names the design
                   (its top / case id) or hard-codes an absolute path;
  * validated   -- the SAME fix (content-addressed recipe_id) was independently verified dual-clean on
                   at least ADMIT_UNITS designs from different split units (repo / identical sources).
  A fix that passes the first two but has fewer independent verifications is stored as a candidate
  (evidence that can later validate it) and is not offered; one that fails them is not stored.

Honesty rules
  * Only a VERIFIED success is distilled: outcome ``dual_clean`` from the harness's own signoff of the
    final layout. ``pending_semantic_review`` (RTL edits awaiting a proof) never produces a recipe.
  * A learned recipe is ``candidate`` until reuse evidence accumulates; ``inject`` only offers it when
    R2G has NO live strategy left for the symptom (it never displaces a built-in or promoted recipe),
    and it rides the existing lifecycle gate as ``candidate_attempt_authorized``.
  * Every reuse outcome is recorded (``record_use``): a failure is negative evidence and a recipe whose
    reuse failures outnumber its supporting evidence (independent donor units + reuse successes) is
    withdrawn.
  * ``retrieve(exclude_units=...)`` drops evidence from the caller's own design unit (repo / identical
    sources), so an evaluation can never "reuse" a recipe learned from the very design under test.

Pure helpers (no I/O): frontend_class, stop_symptom, config_delta, rerun_stage, distill, recipe_id.
DB helpers: ensure_schema, record, record_use, retrieve. Plan helper: inject / as_strategy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

try:  # package or script import
    from . import symptom as _symptom
except ImportError:  # pragma: no cover
    import symptom as _symptom  # type: ignore

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_recipes (
    recipe_id      TEXT PRIMARY KEY,
    symptom_id     TEXT NOT NULL,
    signature_json TEXT NOT NULL,
    platform       TEXT NOT NULL,
    strategy_json  TEXT NOT NULL,          -- {config_edits, rerun_from, recheck, rationale}
    assets_json    TEXT NOT NULL,          -- {name: file content} hooks / override files
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learned_recipe_evidence (
    recipe_id   TEXT NOT NULL REFERENCES learned_recipes(recipe_id),
    kind        TEXT NOT NULL CHECK (kind IN ('distilled', 'reuse_success', 'reuse_failure')),
    design      TEXT NOT NULL,
    unit        TEXT,
    source_json TEXT NOT NULL,             -- attempt provenance + its measured cost
    created_at  TEXT NOT NULL,
    PRIMARY KEY (recipe_id, kind, design)
);
CREATE INDEX IF NOT EXISTS idx_learned_recipes_symptom ON learned_recipes (symptom_id, platform);
"""

# ---------------------------------------------------------------- symptom of R2G's stop
_FRONTEND = [
    ("missing_mem_init_file", re.compile(r"Can not open file .* for \\?\$readmem[hb]")),
    ("missing_include_or_source", re.compile(r"Can not open (include )?file|No such file or directory")),
    ("module_redefinition", re.compile(r"Re-definition of module")),
    ("module_not_found", re.compile(r"Module .* (referenced .* )?(is )?not (part of the design|found)")),
    ("check_assert_problems", re.compile(r"Found \d+ problems in 'check -assert'|Found \d+ problems in .check")),
    ("kernel_assert", re.compile(r"Assert .* failed in kernel")),
    ("abc_failure", re.compile(r"ABC: execution of script")),
    ("syntax_error", re.compile(r"syntax error|Parser error|unexpected")),
    ("tcl_error", re.compile(r"TCL interpreter returned an error|uncaught exception")),
]


def frontend_class(log_text: str | None) -> str | None:
    """Coarse, identifier-free class of the FIRST Yosys ERROR in a synth log (None if no error)."""
    if not log_text:
        return None
    err = next((ln for ln in log_text.splitlines() if "ERROR" in ln), None)
    if err is None:
        return None
    for name, rx in _FRONTEND:
        if rx.search(err):
            return name
    return "other"


def stop_symptom(fix_log_rows: list[dict] | None, synth_log: str | None = None) -> tuple[dict, str] | None:
    """R2G's own symptom at the point it stopped: the last non-clean fix_log row (check, class,
    predicates -> knowledge.symptom signature), else the synth front-end class. None = unknown."""
    bad = [r for r in (fix_log_rows or []) if r.get("verdict") != "stop_clean" and r.get("check")]
    if bad:
        return _symptom.from_fix_log_row(bad[-1])
    fc = frontend_class(synth_log)
    if fc:
        sig = _symptom.canonical_signature("synth", fc, {})
        return sig, _symptom.symptom_id(sig)
    return None


# ---------------------------------------------------------------- the R2G -> LLM delta
# keys whose value only says WHERE the design lives: compared by content, never copied
PLUMBING = {"VERILOG_INCLUDE_DIRS", "PROJECT_ROOT", "PROJ_DIR", "PROJ", "RTL_DIR", "WORK_HOME",
            "FLOW_HOME", "DESIGN_HOME", "DESIGN_DIR"}
FROZEN = {"PLATFORM", "DESIGN_NICKNAME"}           # changing these changes the task


def _sha(b: bytes | str) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def _paths(value: str) -> list[str]:
    return [t for t in str(value).replace("\\\n", " ").split() if "/" in t or t.endswith((".tcl", ".v", ".sv", ".sdc"))]


def _lookup(path: str, files: dict[str, bytes]) -> tuple[str, bytes] | None:
    """Resolve a config path to a file we hold, by its project-relative tail or basename."""
    p = path.rstrip("/")
    for rel in sorted(files, key=len, reverse=True):
        if p.endswith("/" + rel) or p == rel:
            return rel, files[rel]
    bn = os.path.basename(p)
    hits = [rel for rel in files if os.path.basename(rel) == bn]
    return (hits[0], files[hits[0]]) if len(hits) == 1 else None


# A hook that runs no command able to change the design / netlist / layout is an investigation
# artifact (the LLM's probes: inspect_lvs.tcl, diag_drt.tcl, ...), not part of the fix. Anything that
# can run other code (source / eval / exec / uplevel) counts as mutating -- conservative.
_MUTATING = re.compile(
    r"odb::db\w+_(create|destroy|connect|disconnect)"
    r"|\$\w+\s+(connect|disconnect|destroy|swapMaster|move\w*|set[A-Z]\w*)\b"
    r"|(^|[\s\[;{])(insert_buffer|buffer_ports|remove_buffers|repair_\w+|place_\w+|detailed_placement|"
    r"global_placement|global_route|detailed_route|set_dont_touch|unset_dont_touch|set_dont_use|"
    r"add_global_connection|global_connect|add_pdn_\w+|define_pdn_grid|pdngen|tapcell|cut_rows|make_tracks|"
    r"initialize_floorplan|set_placement_padding|filler_placement|remove_fillers|density_fill|write_\w+|"
    r"read_\w+|source|eval|exec|uplevel|techmap|opt\w*|synth\w*|hierarchy|flatten|abc|setattr|setparam|"
    r"delete|rename|chparam|yosys|insert_tiecells|clock_tree_synthesis|set_wire_rc|set_layer_rc|"
    r"set_routing_layers|set_global_routing_layer_adjustment|repair_antennas|rtl_macro_placer|"
    r"macro_placement|set_io_pin_constraint|resize|size_cell|replace_cell|swap_pins|create_\w+|"
    r"connect_\w+|disconnect_\w+|set_\w+_layer\w*)\b", re.M)


def hook_is_read_only(text: str) -> bool:
    body = "\n".join(ln.split("#", 1)[0] if ln.lstrip().startswith("#") else ln for ln in text.splitlines())
    return _MUTATING.search(body) is None


_REF = re.compile(r"^\$[({]([A-Za-z0-9_]+)[)}]$")


def _resolve_refs(cfg: dict) -> dict:
    """Substitute a value that is exactly $(KEY) / ${KEY} by KEY's value (one level, the LLMs' alias idiom)."""
    out = dict(cfg)
    for k, v in cfg.items():
        m = _REF.match(str(v).strip())
        if m and m.group(1) in cfg:
            out[k] = cfg[m.group(1)]
    return out


def config_delta(r2g_cfg: dict, llm_cfg: dict, r2g_files: dict[str, bytes], llm_files: dict[str, bytes]) -> dict:
    """What the LLM changed relative to R2G's final project, with path plumbing factored out.

    Returns {"edits": {KEY: value | "@asset:<name>"}, "assets": {name: text}, "rejected": [reasons],
    "ignored": {KEY: why}}. Files are keyed by project-relative path (constraints/x, hooks/y)."""
    edits, assets, rejected, ignored = {}, {}, [], {}
    aliases = {m.group(1) for v in llm_cfg.values() for m in [_REF.match(str(v).strip())] if m}
    r2g_cfg, llm_cfg = _resolve_refs(r2g_cfg), _resolve_refs(llm_cfg)
    for key in sorted(set(r2g_cfg) | set(llm_cfg)):
        a, b = r2g_cfg.get(key), llm_cfg.get(key)
        if a == b:
            continue
        if key in PLUMBING:
            ignored[key] = "path plumbing"; continue
        if key in FROZEN:
            rejected.append("frozen_key_changed:" + key); continue
        if key == "DESIGN_NAME":
            rejected.append("top_module_changed"); continue
        if b is None:
            ignored[key] = "unset by the LLM (not expressible as an additive edit)"; continue
        pa, pb = _paths(a or ""), _paths(b)
        if key in ("VERILOG_FILES", "SDC_FILE") or (pb and key.endswith(("_FILES", "_FILE")) and key not in ("LATCH_MAP_FILE",)):
            ra = sorted(_sha(x[1]) for x in (_lookup(p, r2g_files) for p in pa) if x)
            rb = sorted(_sha(x[1]) for x in (_lookup(p, llm_files) for p in pb) if x)
            unresolved = len(ra) != len(pa) or len(rb) != len(pb)
            if key == "VERILOG_FILES":
                # the source SET by name: an added / dropped file is a design-specific change
                if sorted(os.path.basename(p) for p in pa) != sorted(os.path.basename(p) for p in pb):
                    rejected.append("source_set_changed")
                else:
                    ignored[key] = "same sources, relocated"
                continue
            if key == "SDC_FILE":
                if unresolved or ra != rb:
                    rejected.append("sdc_changed" if not unresolved else "sdc_unresolvable")
                else:
                    ignored[key] = "same SDC, relocated"
                continue
        if pb:
            # a path-valued knob (hook / override Tcl / map file): capture by CONTENT
            got = [_lookup(p, llm_files) for p in pb]
            if any(g is None for g in got) or len(pb) != 1:
                rejected.append("unresolvable_path:" + key); continue
            rel, data = got[0]
            old = _lookup(pa[0], r2g_files) if len(pa) == 1 else None
            if old is not None and _sha(old[1]) == _sha(data):
                ignored[key] = "same file, relocated"; continue
            name = os.path.basename(rel)
            text = data.decode(errors="replace")
            if key.endswith("_TCL") and name.endswith(".tcl") and hook_is_read_only(text):
                ignored[key] = "read-only hook (investigation, cannot change the design)"; continue
            assets[name] = text
            edits[key] = "@asset:" + name
            continue
        edits[key] = b
    for k in sorted(aliases & set(edits)):
        if any(v == edits[k] for kk, v in edits.items() if kk != k):
            ignored[k] = "alias variable (its value is used through another key)"; edits.pop(k)
    assets = {n: t for n, t in assets.items() if "@asset:" + n in edits.values()}
    return {"edits": edits, "assets": assets, "rejected": rejected, "ignored": ignored}


_STAGES = ["synth", "floorplan", "place", "cts", "route", "finish"]
_STAGE_RULES = [
    ("synth", re.compile(r"^(SYNTH_|ABC_|LATCH_MAP_FILE|PRE_SYNTH|POST_SYNTH|VERILOG_|DONT_USE|ADDER_|TIEHI|TIELO|MAX_UNGROUP)")),
    ("floorplan", re.compile(r"^(CORE_|DIE_|PDN_TCL|TAPCELL_TCL|IO_PLACER|PLACE_PINS|FLOORPLAN|.*_FLOORPLAN_TCL|MACRO_|IO_CONSTRAINTS)")),
    ("place", re.compile(r"^(PLACE_|GPL_|.*_GLOBAL_PLACE_TCL|.*_DETAIL_PLACE_TCL|.*_RESIZE_TCL|CELL_PAD|SKIP_GATE_CLONING|EARLY_SIZING)")),
    ("cts", re.compile(r"^(CTS_|.*_CTS_TCL|HOLD_|SETUP_|SKIP_CTS)")),
    ("route", re.compile(r"^(ROUTING_|GRT_|DRT_|.*_GLOBAL_ROUTE_TCL|.*_DETAIL_ROUTE_TCL|SKIP_ANTENNA|MIN_ROUTING|MAX_ROUTING|DETAILED_ROUTE|GLOBAL_ROUTE)")),
    ("finish", re.compile(r"^(.*_FINAL_REPORT_TCL|.*_DENSITY_FILL_TCL|FILL_|RECOVER_POWER|SKIP_REPORT_METRICS)")),
]


_SIGNOFF_ONLY = re.compile(r"^(LVS_|DRC_|KLAYOUT_|NETGEN_|MAGIC_)")


def rerun_stage(keys) -> str | None:
    """Earliest flow stage a set of edited keys affects; an unknown key means a full rerun (synth).
    Signoff-only knobs (LVS_/DRC_/checker settings) need no flow rerun: None = recheck only."""
    flow = [k for k in keys if not _SIGNOFF_ONLY.match(k)]
    if not flow:
        return None
    best = len(_STAGES) - 1
    for k in flow:
        st = next((s for s, rx in _STAGE_RULES if rx.match(k)), "synth")
        best = min(best, _STAGES.index(st))
    return _STAGES[best]


def recipe_id(platform: str, sid: str, edits: dict, assets: dict) -> str:
    body = json.dumps({"p": platform, "s": sid, "e": edits,
                       "a": {k: _sha(v) for k, v in sorted(assets.items())}}, sort_keys=True)
    return _sha(body)[:16]


MAX_EDITS = 4
ADMIT_UNITS = 2
_GEOMETRY = {"DIE_AREA", "CORE_AREA"}


def known_variables(orfs_root: str | Path | None, skill_dir: str | Path | None = None) -> set[str]:
    """ORFS's documented variables (flow/scripts/variables.yaml top-level keys) plus every
    UPPER_CASE make/env variable R2G's own flow scripts read (e.g. its LVS knobs)."""
    out: set[str] = set()
    if orfs_root:
        y = Path(orfs_root) / "flow" / "scripts" / "variables.yaml"
        if y.exists():
            out |= set(re.findall(r"(?m)^([A-Z][A-Z0-9_]+):", y.read_text(errors="replace")))
    if skill_dir:
        rx = re.compile(r"\$\{?([A-Z][A-Z0-9_]{3,})\b|environ(?:\.get)?[(\[]\s*[\"']([A-Z][A-Z0-9_]{3,})")
        for f in (Path(skill_dir) / "scripts" / "flow").rglob("*"):
            if f.is_file() and f.suffix in (".sh", ".py", ".tcl", ".mk"):
                out |= {a or b for a, b in rx.findall(f.read_text(errors="replace"))}
    return out


def code_only(text: str) -> str:
    """Strip Tcl (#...) and Verilog (//..., /*...*/) comments, keeping strings and code."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    out = []
    for ln in text.splitlines():
        st = ln.lstrip()
        if st.startswith("#") or st.startswith("//"):
            continue
        ln = re.sub(r";\s*#.*$", ";", ln)            # Tcl trailing comment after ';'
        ln = re.sub(r"(^|\s)//.*$", "", ln)           # Verilog trailing comment
        out.append(ln)
    return "\n".join(out)


def assess(edits: dict, assets: dict, *, known_vars: set[str] | None = None, design_tokens=()) -> list[str]:
    """Reasons a verified fix is NOT a reusable recipe (empty list = reasonable + generalizable)."""
    issues = []
    for k, v in edits.items():
        if known_vars is not None and k not in known_vars:
            issues.append("unknown_variable:" + k)
        if k.startswith(("SKIP_", "DISABLE_")) or k.endswith("_VOLTAGES") or \
                (k.startswith("ENABLE_") and str(v).strip() in ("0", "")):
            issues.append("suppresses_repair_or_check:" + k)
        if k in _GEOMETRY:
            issues.append("design_specific_geometry:" + k)
    if len(edits) > MAX_EDITS:
        issues.append("too_broad:%d_edits" % len(edits))
    toks = [t for t in design_tokens if t and len(t) >= 4]
    for name, text in assets.items():
        text = code_only(text)                       # a name in a COMMENT does not make the code specific
        if any(re.search(r"\b%s\b" % re.escape(t), text) for t in toks):
            issues.append("design_specific_identifier:" + name)
        if re.search(r"(?<![\w.])/(proj|data|data2|home|tmp|r2g_toolchain)/", text):
            issues.append("hardcoded_path:" + name)
    return issues


def distill(attempt: dict) -> dict:
    """attempt = {case_id, platform, outcome, rtl_changed, symptom: (sig, sid) | None,
    delta: config_delta(...), summary, source{...}} -> {"recipe": {...}} | {"rejected": reason}."""
    if attempt.get("outcome") != "dual_clean":
        return {"rejected": "not_a_verified_success:" + str(attempt.get("outcome"))}
    if attempt.get("rtl_changed"):
        return {"rejected": "rtl_edit"}
    if not attempt.get("symptom"):
        return {"rejected": "unknown_r2g_symptom"}
    d = attempt["delta"]
    if d["rejected"]:
        return {"rejected": d["rejected"][0]}
    if not d["edits"]:
        return {"rejected": "no_reusable_delta"}
    issues = assess(d["edits"], d["assets"], known_vars=attempt.get("known_vars"),
                    design_tokens=attempt.get("design_tokens") or ())
    if issues:
        return {"rejected": issues[0], "issues": issues}
    sig, sid = attempt["symptom"]
    rid = recipe_id(attempt["platform"], sid, d["edits"], d["assets"])
    recheck = sig.get("check") if sig.get("check") in ("drc", "lvs", "route") else "drc+lvs"
    strategy = {"config_edits": d["edits"], "rerun_from": rerun_stage(d["edits"]), "recheck": recheck,
                "rationale": "Learned from an LLM fallback that closed this symptom after R2G stopped: "
                             + (attempt.get("summary") or "")[:600].strip()}
    return {"recipe": {"recipe_id": rid, "symptom_id": sid, "signature": sig, "platform": attempt["platform"],
                       "strategy": strategy, "assets": d["assets"]}}


# ---------------------------------------------------------------- knowledge DB
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def record(conn: sqlite3.Connection, recipe: dict, design: str, unit: str | None, source: dict) -> bool:
    """Store a distilled recipe (idempotent on recipe_id) + its distillation evidence. True if new."""
    ensure_schema(conn)
    new = conn.execute("SELECT 1 FROM learned_recipes WHERE recipe_id=?", (recipe["recipe_id"],)).fetchone() is None
    if new:
        conn.execute("INSERT INTO learned_recipes VALUES (?,?,?,?,?,?,?)", (
            recipe["recipe_id"], recipe["symptom_id"], json.dumps(recipe["signature"], sort_keys=True),
            recipe["platform"], json.dumps(recipe["strategy"], sort_keys=True),
            json.dumps(recipe["assets"], sort_keys=True), _now()))
    conn.execute("INSERT OR REPLACE INTO learned_recipe_evidence VALUES (?,?,?,?,?,?)", (
        recipe["recipe_id"], "distilled", design, unit, json.dumps(source, sort_keys=True), _now()))
    conn.commit()
    return new


def record_use(conn: sqlite3.Connection, rid: str, design: str, unit: str | None, success: bool, source: dict) -> None:
    ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO learned_recipe_evidence VALUES (?,?,?,?,?,?)", (
        rid, "reuse_success" if success else "reuse_failure", design, unit, json.dumps(source, sort_keys=True), _now()))
    conn.commit()


def retrieve(conn: sqlite3.Connection, sid: str, platform: str, exclude_units=(), exclude_ids=(),
             min_units: int = ADMIT_UNITS) -> list[dict]:
    """VALIDATED recipes for (symptom, platform), best evidence first: at least `min_units` independent
    units verified the identical fix (a candidate below that is stored, not offered). Evidence from
    `exclude_units` is ignored; a recipe whose reuse failures outnumber its donor units + reuse successes
    is withdrawn."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learned_recipes'").fetchone() is None:
        return []                       # a store that never learned a recipe (e.g. the read-only shipped one)
    ex = set(exclude_units or ()); out = []
    for rid, sig, strat, assets in conn.execute(
            "SELECT recipe_id, signature_json, strategy_json, assets_json FROM learned_recipes "
            "WHERE symptom_id=? AND platform=?", (sid, platform)):
        if rid in set(exclude_ids or ()):
            continue
        ev = [(k, u, d) for k, u, d in conn.execute(
            "SELECT kind, unit, design FROM learned_recipe_evidence WHERE recipe_id=?", (rid,)) if u not in ex]
        units = {u or d for k, u, d in ev if k == "distilled"}
        ok = sum(k == "reuse_success" for k, _, _ in ev); bad = sum(k == "reuse_failure" for k, _, _ in ev)
        if len(units) + ok < max(min_units, 1) or bad > len(units) + ok:
            continue                   # withdrawn once reuse failures outweigh ALL supporting evidence
        out.append({"recipe_id": rid, "signature": json.loads(sig), "strategy": json.loads(strat),
                    "assets": json.loads(assets), "evidence_units": len(units), "reuse_success": ok,
                    "reuse_failure": bad})
    out.sort(key=lambda r: (r["reuse_success"] - r["reuse_failure"], r["evidence_units"], r["recipe_id"]), reverse=True)
    return out


# ---------------------------------------------------------------- plan integration
def as_strategy(recipe: dict, project_dir: Path | str, write_assets: bool = True) -> dict:
    """Plan strategy for a retrieved recipe; assets are materialized under <project>/hooks/learned/."""
    proj = Path(project_dir); d = proj / "hooks" / "learned"
    edits = {}
    for k, v in recipe["strategy"]["config_edits"].items():
        if isinstance(v, str) and v.startswith("@asset:"):
            name = v[len("@asset:"):]; text = recipe["assets"][name]
            p = d / ("%s_%s" % (recipe["recipe_id"][:8], name))
            if write_assets:
                d.mkdir(parents=True, exist_ok=True)
                if not p.exists() or p.read_text(errors="replace") != text:
                    p.write_text(text)
            edits[k] = str(p)
        else:
            edits[k] = v
    s = recipe["strategy"]
    return {"id": "learned:" + recipe["recipe_id"], "rationale": s.get("rationale", ""), "config_edits": edits,
            "rerun_from": s.get("rerun_from"), "recheck": s.get("recheck"), "auto_apply": True,
            "requires_ab_promotion": True, "learned_recipe": True,
            "learned_evidence": {"units": recipe["evidence_units"], "reuse_success": recipe["reuse_success"],
                                 "reuse_failure": recipe["reuse_failure"]}}


def inject(plan: dict, project_dir, *, platform: str, sid: str | None, conn: sqlite3.Connection,
           exclude=(), exclude_units=(), live_pick=None, policy_failure=None, write_assets=True,
           min_units: int = ADMIT_UNITS) -> dict:
    """If R2G has no live strategy left for this symptom, offer learned recipes as authorized
    candidate attempts (appended after every built-in strategy, so they never displace one)."""
    if not sid or (live_pick is not None and live_pick(plan) is not None):
        return plan
    added = []
    for rec in retrieve(conn, sid, platform, exclude_units=exclude_units, min_units=min_units):
        st = as_strategy(rec, project_dir, write_assets=write_assets)
        if st["id"] in set(exclude or ()):
            continue
        why = policy_failure(st) if policy_failure else None
        if why:
            plan.setdefault("learned_rejected", []).append({"id": st["id"], "reason": why}); continue
        st["candidate_attempt_authorized"] = True
        st["lifecycle_status"] = "candidate"
        added.append(st)
    if added:
        plan.setdefault("strategies", []).extend(added)
        plan["learned_offered"] = [s["id"] for s in added]
        if plan.get("status") in ("residual", None) or plan.get("residual_reason"):
            plan["residual_reason"] = None
    return plan
