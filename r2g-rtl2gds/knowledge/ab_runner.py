#!/usr/bin/env python3
"""Inline recipe A/B planner + judge (engineer-loop spec §5.4).

plan_trial(): pick matched designs from run_violations history (same symptom,
decision-8 relaxation, CHEAPEST first — Phase-0 small-design-first), and define
the two arms. The ORCHESTRATOR executes arms as ordinary ledger entries with
distinct FLOW_VARIANT project dirs; this module never runs flows.

judge(): honest verdict — arm B must be a USABLE signed-off result AND better
(cheaper wall-clock, or equal-cost with fewer fix iters). Both-fail or crashed
arm -> inconclusive, NEVER a win (inherits eval_heuristics invariant 11).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import statistics

import recipe_lifecycle

HEUR_PATH = os.path.join(os.path.dirname(__file__), "heuristics.json")

# An A/B trial copies each subject to <name>_ab{A,B}_<strat8>_<r>. Those copies get
# ingested (they carry the recipe's symptom), so without this guard plan_trial would
# re-select an arm dir as a SUBJECT — copying it again into <...>_abA_..._abA_... and
# polluting run_violations with ever-deeper nests. A/B subjects must be REAL designs.
_ARM_DIR_RE = re.compile(r"_ab[AB]_")


def _is_arm_dir(project_path: str | None) -> bool:
    return bool(project_path) and bool(_ARM_DIR_RE.search(os.path.basename(project_path)))

N_DESIGNS_DEFAULT = 2     # min matched designs per trial (spec §5.4)
AB_REPEATS_DEFAULT = 2    # Win 2: k repeats per arm for variance-aware promotion
AB_LCB_Z = 1.0            # z for the lower-confidence bound (mean − z·stderr)


def ab_repeats() -> int:
    """k repeats per arm (env R2G_AB_REPEATS, default 2). k=2 bounds the k×
    wall-clock multiplier on the already-slow A/B path; k=3 is opt-in for
    high-stakes promotions. <1 is clamped to 1."""
    try:
        return max(1, int(os.environ.get("R2G_AB_REPEATS", AB_REPEATS_DEFAULT)))
    except (TypeError, ValueError):
        return AB_REPEATS_DEFAULT


def lcb(samples: list[float], z: float = AB_LCB_Z) -> float:
    """Lower confidence bound = mean − z·stderr. Penalizes a high-variance arm so
    one lucky win cannot promote a recipe (the documented LVS-crash heisenbug).
    A single sample has stderr 0 -> returns the mean; empty -> 0.0."""
    if not samples:
        return 0.0
    n = len(samples)
    mean = sum(samples) / n
    if n < 2:
        return mean
    stderr = statistics.stdev(samples) / (n ** 0.5)
    return mean - z * stderr


def judge_repeated(arm_a_samples: list[dict | None],
                   arm_b_samples: list[dict | None], *,
                   z: float = AB_LCB_Z) -> str:
    """Variance-aware verdict over k repeats per arm (Win 2). Each sample is an
    arm-result dict {is_success, wall_s?, fix_iters?, outcome_score?} or None
    (crash). Promotion (`win`) requires arm B to sign off at least once AND a
    higher LCB over the binary success-rate than arm A — never a single lucky run.
    Degrades to the single-run binary verdict when each arm has one sample.

    is_success stays the sole authority for a win: a never-clean arm B can never
    win (invariant H4); outcome_score is NOT used to flip the verdict."""
    a = [s for s in arm_a_samples if s is not None]
    b = [s for s in arm_b_samples if s is not None]
    if not a or not b:
        return "inconclusive"                 # an arm produced no judgeable result
    a_succ = [1.0 if s.get("is_success") else 0.0 for s in a]
    b_succ = [1.0 if s.get("is_success") else 0.0 for s in b]
    if sum(b_succ) == 0:                       # B never signed off -> never a win
        return "inconclusive" if sum(a_succ) == 0 else "loss"
    lcb_a, lcb_b = lcb(a_succ, z), lcb(b_succ, z)
    if lcb_b > lcb_a:
        return "win"
    if lcb_b < lcb_a:
        return "loss"
    # Tie on success LCB (e.g. both reliably clean): the cheaper mean wall-clock
    # wins, mirroring the single-run judge's cost tiebreaker.
    wa = [s["wall_s"] for s in a if s.get("wall_s") is not None]
    wb = [s["wall_s"] for s in b if s.get("wall_s") is not None]
    if wa and wb:
        ma, mb = statistics.mean(wa), statistics.mean(wb)
        if mb < ma * 0.98:
            return "win"
        if mb > ma * 1.02:
            return "loss"
    return "inconclusive"


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _evidence_designs(symptom_id: str) -> list[str]:
    """Designs the learner recorded as having EXHIBITED this symptom (pre-fix)."""
    try:
        with open(HEUR_PATH) as fh:
            heur = json.load(fh)
    except (OSError, ValueError):
        return []
    sym = (heur.get("symptoms") or {}).get(symptom_id) or {}
    return list(sym.get("evidence_designs") or [])


def _resolve_evidence(conn, ev_names: list[str], want_platform: str | None) -> list[dict]:
    """Map recipe evidence-design names -> on-disk re-runnable project dirs.

    fix_events/heuristics record the project-dir basename as the design name; the
    runs table carries the absolute project_path. We match a runs row whose
    project_path basename is an evidence name (optionally stripping a `__<plat>`
    suffix), keep the latest row per project, require the dir to still exist, and
    order cheapest-first (Phase-0 small-design-first)."""
    if not ev_names:
        return []
    names = set(ev_names)
    rows = conn.execute(
        "SELECT design_name, project_path, cell_count, platform, "
        "ROW_NUMBER() OVER (PARTITION BY project_path ORDER BY ingested_at DESC, run_id DESC) rn "
        "FROM runs").fetchall()
    out, seen = [], set()
    for design_name, project_path, cell_count, plat, rn in rows:
        if rn != 1 or not project_path or project_path in seen:
            continue
        base = os.path.basename(project_path.rstrip("/"))
        if _is_arm_dir(base):
            continue                       # never A/B an A/B arm copy
        stem = base.split("__", 1)[0]
        if base not in names and stem not in names:
            continue
        if want_platform and plat != want_platform:
            continue
        if not os.path.isdir(project_path):
            continue
        seen.add(project_path)
        out.append({"design_name": design_name, "project_path": project_path,
                    "cell_count": cell_count or 0})
    out.sort(key=lambda d: d["cell_count"])
    return out


def _symptom_designs(conn, symptom_id: str, want_platform: str | None) -> list[dict]:
    """Designs that DEMONSTRABLY exhibited this symptom, taken from the fix history.

    A successfully-fixed symptom (e.g. ``antenna_diode_repair`` clearing DRC to 0)
    leaves NO row in run_violations — that table is the POST-fix residual snapshot,
    so plan_trial's Tier 1 is structurally blind to exactly the recipes that work.
    But fix_trajectories/fix_events recorded the precise ``project_path`` that hit
    the symptom (resolved OR abandoned), keyed by ``symptom_id``. Resolving from
    there is symptom-confirmed AND on-disk-exact — strictly better than the
    heuristics ``evidence_designs`` name-list (Tier 3 below), which stores the bare
    DESIGN_NAME (``can_tx``) and so (a) misses the campaign's repo-prefixed project
    dirs (``CAN_Bus_Controller_can_tx``) and (b) collides with generic module names
    (``test``/``top``) shared by dozens of unrelated designs.

    (2026-06-22: this was the third reason the live A/B loop never fired — after
    Gate A and the run_violations-only gap — and the one that kept every *successful*
    nangate45 recipe, antenna chief among them, permanently stuck as ``candidate``.)
    """
    paths: set[str] = set()
    for tbl in ("fix_trajectories", "fix_events"):
        try:
            for (pp,) in conn.execute(
                    f"SELECT DISTINCT project_path FROM {tbl} WHERE symptom_id=?",
                    (symptom_id,)):
                if pp:
                    paths.add(pp)
        except sqlite3.OperationalError:
            pass                              # table/column absent on a legacy DB
    if not paths:
        return []
    # Join through runs for cell_count/platform and latest-row-per-project, mirroring
    # _resolve_evidence (cheapest-first, real dirs only, never an A/B arm copy).
    rows = conn.execute(
        "SELECT design_name, project_path, cell_count, platform, "
        "ROW_NUMBER() OVER (PARTITION BY project_path ORDER BY ingested_at DESC, run_id DESC) rn "
        "FROM runs").fetchall()
    out, seen = [], set()
    for design_name, project_path, cell_count, plat, rn in rows:
        if rn != 1 or project_path not in paths or project_path in seen:
            continue
        if _is_arm_dir(project_path):
            continue
        if want_platform and plat != want_platform:
            continue
        if not os.path.isdir(project_path):
            continue
        seen.add(project_path)
        out.append({"design_name": design_name, "project_path": project_path,
                    "cell_count": cell_count or 0})
    out.sort(key=lambda d: d["cell_count"])
    return out


def plan_trial(conn, *, symptom_id: str, design_class: str, platform: str,
               strategy: str, n_designs: int = N_DESIGNS_DEFAULT) -> dict | None:
    """Returns {designs, arm_a, arm_b, match_level} or None if no match."""
    def _q(extra_sql: str, params: tuple) -> list[dict]:
        cur = conn.execute(
            "SELECT r.design_name, r.project_path, r.cell_count "
            "FROM run_violations v JOIN runs r USING(run_id) "
            f"WHERE v.symptom_id=? {extra_sql} "
            "GROUP BY r.design_name ORDER BY MIN(r.cell_count)",
            (symptom_id, *params))
        return [dict(zip(("design_name", "project_path", "cell_count"), x))
                for x in cur.fetchall() if not _is_arm_dir(x[1])]

    def _trial(designs, level):
        return {
            "designs": designs[:n_designs],
            "match_level": level,
            "arm_a": {"exclude_strategy": strategy},
            "arm_b": {"rank_first_strategy": strategy},
            "key": {"symptom_id": symptom_id, "design_class": design_class,
                    "platform": platform, "strategy": strategy},
        }

    # Tier 1 — run_violations (POST-fix residual exhibitors of the symptom).
    for extra, params, level in (
            ("AND r.design_class=? AND r.platform=?", (design_class, platform),
             "exact"),
            ("AND r.platform=?", (platform,), "pooled_class"),
            ("", (), "pooled_platform")):
        designs = _q(extra, params)
        if len(designs) >= n_designs:
            return _trial(designs, level)

    # Tier 2 — fix-history exhibitors (symptom-confirmed, on-disk-precise). A recipe
    # that SUCCEEDS clears the symptom, so Tier 1 (run_violations residuals) is empty
    # for exactly the recipes worth promoting. fix_trajectories/fix_events recorded
    # the precise project that hit this symptom_id; resolve straight from there.
    # (2026-06-22: without this, every successful nangate45 recipe — antenna chief
    # among them — was unreachable and stuck forever as a candidate.)
    for want_plat, level in ((platform, "fixhist_platform"), (None, "fixhist_pooled")):
        designs = _symptom_designs(conn, symptom_id, want_plat)
        if len(designs) >= n_designs:
            return _trial(designs, level)

    # Tier 3 — recipe evidence designs (PRE-fix exhibitors). Last resort: the learner
    # records who exhibited the symptom in heuristics.symptoms[sid].evidence_designs,
    # but as bare DESIGN_NAMEs, so this resolves only legacy same-name project dirs.
    # (2026-06-16: this gap, on top of Gate A, was the second reason the A/B loop had
    # never fired; Tier 2 above now covers the repo-prefixed campaign dirs it misses.)
    for want_plat, level in ((platform, "evidence_platform"), (None, "evidence_pooled")):
        designs = _resolve_evidence(conn, _evidence_designs(symptom_id), want_plat)
        if len(designs) >= n_designs:
            return _trial(designs, level)
    return None


def judge(arm_a: dict | None, arm_b: dict | None) -> str:
    """arm dicts: {is_success: bool, wall_s: float|None, fix_iters: int|None}.
    None = the arm crashed / produced no judgeable result."""
    if arm_a is None or arm_b is None:
        return "inconclusive"
    if not arm_b.get("is_success"):
        return "inconclusive" if not arm_a.get("is_success") else "loss"
    if not arm_a.get("is_success"):
        return "win"                      # B usable where A was not
    wa, wb = arm_a.get("wall_s"), arm_b.get("wall_s")
    if wa is not None and wb is not None and wb < wa * 0.98:
        return "win"
    ia, ib = arm_a.get("fix_iters"), arm_b.get("fix_iters")
    if ia is not None and ib is not None and ib < ia:
        return "win"
    if wa is not None and wb is not None and wb > wa * 1.02:
        return "loss"
    return "inconclusive"


def _journal_verdict(key: dict, verdict: str, tid: int) -> None:
    """Best-effort Tier-B2 journal of the A/B promote/demote DECISION. ADVISORY only
    — knowledge.sqlite (ab_trials + recipe_status) stays the sole source of truth, so
    a silenced or failed journal write must never affect the verdict. Carries
    trial_id so each winning trial maps to exactly one promote action (acceptance
    #4). Runs in record_trial's SERIAL post-join section (engineer-loop spec)."""
    if os.environ.get("R2G_JOURNAL", "1") == "0":
        return
    try:
        import journal_db
        conn = journal_db.connect(
            os.environ.get("R2G_JOURNAL_DB") or journal_db.DEFAULT_JOURNAL_PATH)
        journal_db.ensure_schema(conn)
        atype = "promote" if verdict == "win" else "demote"
        journal_db.append_action(
            conn, project_path="", actor="loop", action_type=atype,
            design=f"recipe:{key['strategy']}", platform=key.get("platform"),
            payload={"strategy": key["strategy"], "symptom_id": key["symptom_id"],
                     "design_class": key.get("design_class"),
                     "trial_id": tid, "verdict": verdict},
            symptom_id=key["symptom_id"])
        conn.close()
    except Exception:                          # telemetry must never break the verdict
        pass


def record_trial(conn, *, key: dict, verdict: str, arm_a_run_id: str | None,
                 arm_b_run_id: str | None, metrics: dict,
                 match_level: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "arm_a_run_id, arm_b_run_id, verdict, metrics_json, match_level, ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], arm_a_run_id, arm_b_run_id, verdict,
         json.dumps(metrics, sort_keys=True), match_level, _now()))
    conn.commit()
    tid = cur.lastrowid
    if verdict == "win":
        recipe_lifecycle.promote(conn, evidence=f"ab_trial:{tid}", **key)
    else:
        recipe_lifecycle.demote(conn, reason=f"ab_{verdict}:{tid}", **key)
    _journal_verdict(key, verdict, tid)        # advisory journal (Tier B2)
    return tid


def auto_demote_on_regression(conn, *, key: dict, window: int = 2) -> bool:
    """Spec §7: a PROMOTED recipe with `window` consecutive live regressions on
    its symptom is auto-demoted + escalated. Counts recent fix_events for this
    strategy+symptom; returns True if demoted."""
    rows = conn.execute(
        "SELECT verdict FROM fix_events WHERE symptom_id=? AND strategy=? "
        "ORDER BY fix_event_id DESC LIMIT ?",
        (key["symptom_id"], key["strategy"], window)).fetchall()
    if len(rows) == window and all(r[0] == "regression" for r in rows):
        recipe_lifecycle.demote(conn, reason="repeated_regression", **key)
        import escalations
        escalations.open_escalation(
            conn, design=f"recipe:{key['strategy']}", project_path="",
            run_id=None, reason="repeated_regression",
            symptom_id=key["symptom_id"],
            notes=json.dumps(key, sort_keys=True))
        return True
    return False
