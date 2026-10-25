"""Neutral experience export and re-import (P1c; scheme memory/evaluation/r2g_P0_neutral_experience_export_scheme_20261008.md,
plan memory/evaluation/r2g_P1c_export_plan_20261009.md).

One exported UNIT = one physical repair attempt, built from the RAW fix-log rows (never from TEHM's tables or
rules), so every memory method reads the same experience. A unit carries:

- ``unit_id`` / ``identity``: the capture identity (design, platform, fix session, check, iteration, log time),
  the same one TEHM capture uses (``r2g_evidence.record_identity``);
- ``raw_row``: the row exactly as logged (the resolved variant when copies conflict), ``content_digest`` its sha256;
- ``capture_context``: what the record builder otherwise derives from the whole session (previous cumulative
  config, step position, terminal flag, the session's verdicts), computed ONCE from the complete raw session.
  A unit therefore imports identically whatever batch it arrives in (exit condition X1);
- ``outcome``: the verdict as logged and the canonical outcome of the shared adapter (``label_as_recorded``;
  never upgraded to a newer acceptance checklist, whose status stays ``UNKNOWN``);
- ``rollback``: the status as recorded (``true`` / ``false`` / ``UNKNOWN``) and the reason, plus the raw evidence
  that could support recovering an UNKNOWN status -- recorded, NOT applied (the recovery bases await the user's
  decision);
- ``conflict``: when copies of the same attempt differ. Every variant is kept (``raw_variants``). Annotation-only
  differences are unified by the frozen shared converter (``ann-v1``, below; never "newest version wins" or
  path order), and ``raw_row`` is then the canonical row it produces; a difference in a measured field, or copies
  whose session contexts are not nested, excludes the unit from SOURCE (``source_eligible`` false);
- ``lineage``, ``cost`` (UNKNOWN where not recorded), ``provenance`` (every raw copy: path, line, digest),
  ``context`` (``dialogue: missing`` unless an original transcript is linked) and a uniform ``text_view``.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

SCHEMA = "r2g-neutral-export-v1"
# Fields whose differences between copies of one attempt are annotations, not measurements.
ANNOTATION_FIELDS = frozenset({"situation", "strategy", "strategy_family", "seed"})


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(obj) -> str:
    return "sha256:" + hashlib.sha256(_canonical(obj).encode()).hexdigest()


# -- raw sources ---------------------------------------------------------------------------------------------

def read_fix_log_source(path: Path) -> dict:
    """{path, design, platform, project, rows: [(line_no, row)]} of one project's reports/fix_log.jsonl."""
    from tehm.adapters.r2g_evidence import parse_config_mk
    path = Path(path)
    project = path.parent.parent
    cfg_path = project / "constraints" / "config.mk"
    cfg = parse_config_mk(cfg_path.read_text(errors="ignore")) if cfg_path.is_file() else {}
    rows = []
    for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        if line.strip().startswith("{"):
            try:
                rows.append((i, json.loads(line)))
            except ValueError:
                continue
    return {"path": str(path), "design": cfg.get("DESIGN_NAME"), "platform": cfg.get("PLATFORM"),
            "project": project.name, "rows": rows}


def read_trial_source(path: Path, *, design: str, platform: str) -> dict:
    """A component-trial file (a JSON list of fix-log-shaped rows)."""
    path = Path(path)
    data = json.loads(path.read_text())
    return {"path": str(path), "design": design, "platform": platform, "project": path.stem,
            "rows": [(i, r) for i, r in enumerate(data, 1) if isinstance(r, dict)]}


# -- unit construction ---------------------------------------------------------------------------------------

def _origin(row: dict) -> str:
    s = str(row.get("strategy") or "")
    if row.get("evidence_tier") == "component_trial" or s.startswith("component:"):
        return "component_trial"
    if s.startswith("tehm_") or row.get("memory_trial"):
        return "memory"
    if s.startswith("llm") or row.get("llm"):
        return "llm"
    return "catalogue"


def _edit_in(cfg, row) -> bool | None:
    from tehm.adapters.r2g_evidence import normalize_knob_edits
    edits = normalize_knob_edits(row.get("config_delta"), row.get("config_before"))
    if not edits or not isinstance(cfg, dict):
        return None
    return all((str(cfg.get(k)) == str(v["after"])) if v["after"] is not None else cfg.get(k) in (None, "")
               for k, v in edits.items())


def _recovery_evidence(rows: list[dict], i: int, ref: dict) -> list[dict]:
    """Raw evidence that the i-th step of a session was NOT rolled back (recorded, not applied)."""
    row, out = rows[i], []
    if row.get("evidence_tier") == "component_trial":
        out.append({"basis": "trial_isolated", "ref": ref})
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    if nxt is not None and _edit_in(nxt.get("config_before"), row):
        out.append({"basis": "next_step_config", "ref": {**ref, "next_iter": nxt.get("iter")}})
    cum = row.get("cumulative_config")
    try:
        cum = json.loads(cum) if isinstance(cum, str) else cum
    except ValueError:
        cum = None
    if _edit_in(cum, row):
        out.append({"basis": "log_cumulative", "ref": ref,
                    "caveat": "valid only for code versions that log after the rollback"})
    return out


def _text_view(u: dict) -> str:
    r, v = u["raw_row"], u["verification"]
    sit = r.get("situation") if isinstance(r.get("situation"), dict) else {}
    sit_s = ", ".join("%s=%s" % (k, sit[k]) for k in sorted(sit) if sit[k] not in (None, "") and k != "v")
    return ("State: design %s on %s; check %s, class %s, count %s%s. "
            "Action (%s): %s, edits %s, rerun from %s. "
            "Verification: %s -> %s (verdict as logged: %s). Canonical outcome: %s (%s). "
            "Rolled back: %s%s.") % (
        u["identity"]["design"], u["identity"]["platform"], v["check"], r.get("violation_class"), v["before"],
        ("; situation " + sit_s) if sit_s else "", u["action"]["origin"], u["action"]["strategy"],
        _canonical(u["action"]["config_delta"]), u["action"]["from_stage"], v["before"], v["after"],
        u["outcome"]["verdict_as_logged"], u["outcome"]["canonical"],
        "verified" if u["outcome"]["verified"] else "not verified", u["rollback"]["rolled_back"],
        (" (" + u["rollback"]["reason"] + ")") if u["rollback"]["reason"] else "")


# -- shared annotation converter (user decision D-R3, 2026-10-09) ----------------------------------------------
#
# ann-v1: ONE frozen converter, used for every unit and every method, producing the unified annotation from the
# raw state captured BEFORE the action. It never picks a variant because it is newer or comes first by path.
#   situation: the sit-v2 schema of r2g knowledge/situation.py (``canonical``). sit-v2 = sit-v1 + perimeter_band,
#   which is non-null only for check orfs_stage with error code PPL-0024. Per field:
#     check, violation_class   the raw row;  platform   the raw source's config;
#     count_band               recomputed from the raw before-count -- except on rows whose ``before`` is the LLM
#                              arm's binary closure flag (``llm: true`` rows, seed LLM rows: before = 1 means "not
#                              clean"), where it is the recorded pre-action annotation when every copy agrees and is
#                              not null (a null there is the seeder's omission at later iterations), else UNKNOWN
#                              (semantic validation on the corpus: recomputing from the flag disagreed 43/2023);
#     die_mode, util_band, error_code, timeout
#                              the recorded pre-action annotation (the writers compute it from the pre-action state:
#                              fix_signoff via situation.from_project BEFORE the edit; the seeder from the task's
#                              qualification project) when every copy agrees, else UNKNOWN;
#     perimeter_band           None by definition outside PPL-0024; for PPL-0024 recomputed from the raw pre-action
#                              run when the resolver locates it (a recorded value must agree, else UNKNOWN), else the
#                              value recorded at action time by a sit-v2 writer (not a seeder), else UNKNOWN.
#   UNKNOWN is the literal string "UNKNOWN": a rule precondition on it never matches a live situation, which
#   isolates such units' scope.
#   strategy: the recorded label when every copy agrees; differing labels merge only because the action itself
#   (edits, prior values, rerun stage, check, counts, verdict) is identical -- then the label derived from the action
#   ("llm_edit:" + sorted edited knobs, the seeder's D-A1 definition) when it is one of the recorded labels, else
#   UNKNOWN. Every original annotation is kept in ``annotations``.
ANNOTATION_CONVERTER = "ann-v1"
UNKNOWN = "UNKNOWN"
_SIT_RECORDED = ("die_mode", "util_band", "error_code", "timeout")


class R2GPreActionResolver:
    """Locates the raw pre-action state of seed rows (the R2G evaluation layout): the task's qualification project
    (seed round -> qualification summary) and, for LLM-arm rows, the original arm record's iteration."""
    QUAL = {1: "qualification", 2: "qualification-r2", 3: "qualification-d2hd", 4: "qualification-d2hs"}

    def __init__(self, eval_root: Path):
        self.root = Path(eval_root)
        self._qual: dict = {}

    def _seed_task(self, row: dict, project: str):
        seed = str(row.get("seed") or "")
        parts = seed.split(":")
        bits = str(project).split("__")
        if len(parts) < 3 or not parts[0].startswith("r") or len(bits) < 3:
            return None
        try:
            rnd = int(parts[0][1:])
        except ValueError:
            return None
        return rnd, parts[1], parts[2], bits[1], bits[2]

    def qualification_project(self, rnd: int, design: str, variant: str) -> Path | None:
        if rnd not in self._qual:
            q = self.root / self.QUAL.get(rnd, "_none") / "summary.json"
            recs = json.loads(q.read_text())["records"] if q.is_file() else []
            self._qual[rnd] = {(r["design"], r["variant"]): Path(r["project"]) for r in recs}
        p = self._qual[rnd].get((design, variant))
        return p if p is not None and p.exists() else None

    def llm_iteration(self, src: str, design: str, variant: str, config_delta) -> int | None:
        f = self.root / src / ("%s__%s__L.json" % (design, variant))
        if not f.is_file():
            return None
        delta = {k: (None if v is None else str(v)) for k, v in (config_delta or {}).items()}
        hits = []
        for it in json.loads(f.read_text()).get("iterations") or []:
            ap = it.get("applied") or {}
            sets = {k: str(v) for k, v in (ap.get("set") or {}).items()}
            sets.update({k: None for k in ap.get("unset") or []})
            if ap and sets == delta:
                hits.append(int(it["iter"]))
        return hits[0] if len(hits) == 1 else None

    def pre_action_perimeter(self, row: dict, project: str) -> tuple[float | None, str]:
        """(ratio, how) from the raw pre-action run, or (None, reason)."""
        task = self._seed_task(row, project)
        if task is None:
            return None, "no_resolver_for_non_seed_row"
        rnd, kind, src, design, variant = task
        if kind == "L" and self.llm_iteration(src, design, variant, row.get("config_delta")) != 1:
            return None, "llm_iteration_after_1: pre-action run not established"
        qp = self.qualification_project(rnd, design, variant)
        if qp is None:
            return None, "qualification_project_missing"
        ratio = _sitmod().backend_facts(qp).get("perimeter_ratio")
        return (ratio, "qualification_run:%s" % qp) if ratio else (None, "no_perimeter_message_in_qualification_run")


def _sitmod():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "r2g-skills" / "signoff-loop" / "knowledge"))
    import situation
    return situation


def unified_situation(rows: list[dict], *, platform: str, project: str, resolver=None) -> tuple[dict, dict]:
    """(sit-v2 situation, per-field provenance) from the copies of one attempt (identical measured fields)."""
    sm = _sitmod()
    base = rows[0]
    recorded = [r.get("situation") for r in rows if isinstance(r.get("situation"), dict)]
    check = str(base.get("check") or "")
    vclass = base.get("violation_class")
    if check == "route":
        check, vclass = "orfs_stage", (vclass or "route")
    out = {"v": "sit-v2", "check": check, "violation_class": vclass or None, "platform": platform or None}
    prov = {"check": "raw_row", "violation_class": "raw_row", "platform": "raw_source"}
    binary_before = base.get("llm") is True or str(base.get("seed") or "").split(":")[1:2] == ["L"]
    if check not in ("drc", "lvs"):
        out["count_band"], prov["count_band"] = None, "definition"
    elif not binary_before:
        out["count_band"], prov["count_band"] = sm.count_band(base.get("before")), "raw_row"
    else:
        vals = {_canonical(x.get("count_band")) for x in recorded}
        if recorded and len(vals) == 1 and recorded[0].get("count_band") is not None:
            out["count_band"], prov["count_band"] = recorded[0]["count_band"], "recorded_pre_action"
        else:
            out["count_band"], prov["count_band"] = UNKNOWN, ("binary_before_flag_no_recorded_count"
                                                             if recorded else "no_recorded_annotation")
    for f in _SIT_RECORDED:
        vals = {_canonical(s.get(f)) for s in recorded}
        if recorded and len(vals) == 1:
            out[f] = recorded[0].get(f)
            prov[f] = "recorded_pre_action"
        else:
            out[f], prov[f] = UNKNOWN, ("no_recorded_annotation" if not recorded else "copies_disagree")
    if out["check"] == "orfs_stage" and out["error_code"] == "PPL-0024":
        v2_vals = [s.get("perimeter_band") for s in recorded if s.get("v") == "sit-v2" and "perimeter_band" in s]
        ratio, how = (resolver.pre_action_perimeter(base, project) if resolver else (None, "no_resolver"))
        if ratio:
            band = sm.perimeter_band(ratio)
            agree = all(v == band for v in v2_vals if v is not None)
            out["perimeter_band"] = band if agree else UNKNOWN
            prov["perimeter_band"] = ("recomputed:" + how) if agree else "recomputed_disagrees_with_recorded"
        elif v2_vals and not base.get("seed") and len({_canonical(v) for v in v2_vals}) == 1 and v2_vals[0] is not None:
            out["perimeter_band"], prov["perimeter_band"] = v2_vals[0], "recorded_at_action_sit_v2"
        else:
            out["perimeter_band"], prov["perimeter_band"] = UNKNOWN, how
    else:
        out["perimeter_band"], prov["perimeter_band"] = None, "definition"
    return out, prov


def unified_strategy(rows: list[dict]) -> tuple[str, str]:
    labels = sorted({str(r.get("strategy")) for r in rows})
    if len(labels) == 1:
        return labels[0], "recorded"
    derived = "llm_edit:" + ",".join(sorted((rows[0].get("config_delta") or {})))
    if derived in labels:
        return derived, "derived_from_identical_action"
    return UNKNOWN, "labels_differ_no_action_derived_label"


def _merge(occurrences: list[dict], *, platform: str, project: str, resolver=None):
    """(canonical row, chosen occurrence, annotations, conflict) for the copies of one attempt."""
    by_digest: dict[str, dict] = {}
    for o in occurrences:
        by_digest.setdefault(o["digest"], {"row": o["row"], "digest": o["digest"], "refs": []})["refs"].append(o["ref"])
    variants = list(by_digest.values())
    keys = set().union(*[v["row"].keys() for v in variants])
    fields = sorted(k for k in keys if len({_canonical(v["row"].get(k)) for v in variants}) > 1)
    measured = [f for f in fields if f not in ANNOTATION_FIELDS]
    # session context: the copy whose session contains every other copy's rows (a crashed copy holds a prefix)
    chosen = next((o for o in occurrences if all(set(x["session_ids"]) <= set(o["session_ids"]) for x in occurrences)),
                  None)
    if chosen is not None and any(_canonical(x["ctx"]) != _canonical(chosen["ctx"]) for x in occurrences
                                  if set(x["session_ids"]) == set(chosen["session_ids"])):
        chosen = None
    rows = [v["row"] for v in variants]
    conflict = None
    if fields or chosen is None:
        conflict = {"kind": "measured" if measured else ("context" if chosen is None else "annotation"),
                    "fields": fields,
                    "variants": [{"digest": v["digest"], "refs": v["refs"],
                                  "values": {f: v["row"].get(f) for f in fields}} for v in variants]}
    if measured or chosen is None:
        conflict["resolution"] = "none: unit excluded from SOURCE, all variants kept"
        return None, chosen or occurrences[0], {}, conflict
    sit, sit_prov = unified_situation(rows, platform=platform, project=project, resolver=resolver)
    strategy, strat_prov = unified_strategy(rows)
    canonical = {k: v for k, v in rows[0].items() if k not in ANNOTATION_FIELDS}
    canonical.update(situation=sit, strategy=strategy)
    fam = {_canonical(r.get("strategy_family")) for r in rows}
    if len(fam) == 1 and rows[0].get("strategy_family") is not None:
        canonical["strategy_family"] = rows[0]["strategy_family"]
    annotations = {"converter": ANNOTATION_CONVERTER, "situation_provenance": sit_prov, "strategy_provenance": strat_prov,
                   "recorded": [{"digest": v["digest"], **{f: v["row"].get(f) for f in sorted(ANNOTATION_FIELDS)
                                                           if f in v["row"]}} for v in variants]}
    if conflict:
        conflict["resolution"] = "annotation-only: unified by the frozen converter %s; all variants kept" % ANNOTATION_CONVERTER
    return canonical, chosen, annotations, conflict


def build_units(sources: list[dict], *, resolver=None) -> tuple[list[dict], dict]:
    """Units from raw sources, one per physical attempt; returns (units, dedup log)."""
    from tehm.adapters.r2g_evidence import (build_execution_records, record_identity, normalize_knob_edits,
                                            _parse_json_field)
    from tehm.canonical.transition import ObservationDelta, classify_outcome
    from tehm.canonical.verifier import VerifierSnapshot
    occ: dict[str, dict] = {}
    for src in sources:
        sessions = collections.defaultdict(list)
        for line, row in src["rows"]:
            if row.get("fix_session_id"):
                sessions[str(row["fix_session_id"])].append((line, row))
        for sid, lst in sessions.items():
            lst.sort(key=lambda x: int(x[1].get("iter", 0) or 0))
            rows = [r for _, r in lst]
            ids = []
            for i, (_, row) in enumerate(lst):
                iteration = int(row["iter"]) if str(row.get("iter", "")).strip().lstrip("-").isdigit() else i
                ids.append(record_identity(design=src["design"], platform=src["platform"], session=sid,
                                           check=row.get("check"), iteration=iteration, ts=row.get("ts")))
            cfg = {"DESIGN_NAME": src["design"], "PLATFORM": src["platform"]}
            records = build_execution_records({"project": Path("/export") / str(src["design"]), "config": cfg,
                                               "reports": {}, "stage_log": [], "fix_log": rows})
            prev = None
            for i, ((line, row), rec, (uid, scope)) in enumerate(zip(lst, records, ids)):
                ref = {"path": src["path"], "line": line}
                ctx = {"prev_cumulative": prev, "step_index": i, "is_terminal": i == len(rows) - 1,
                       "session_verdicts": [r.get("verdict") for r in rows]}
                entry = occ.setdefault(uid, {"identity": scope, "project": src["project"],
                                             "platform": src["platform"], "list": []})
                entry["list"].append({
                    "row": row, "digest": _digest(row), "ref": ref, "ctx": ctx,
                    "session_ids": [x for x, _ in ids],
                    "outcome": classify_outcome(ObservationDelta.from_dict(rec.observation_delta),
                                                VerifierSnapshot.from_dict(rec.verification)),
                    "verified": bool(rec.verification.get("oracle_complete")),
                    "recovery": _recovery_evidence(rows, i, ref)})
                prev = _parse_json_field(row.get("cumulative_config")) or \
                    {**cfg, **{k: e["after"] for k, e in normalize_knob_edits(row.get("config_delta")).items()}}
    units, log = [], {"units": 0, "copies_merged": 0, "conflicts_annotation": 0, "conflicts_measured": 0,
                      "conflicts_context": 0, "situation_unknown_fields": collections.Counter()}
    for uid in sorted(occ):
        entry = occ[uid]
        canonical, chosen, annotations, conflict = _merge(entry["list"], platform=entry["platform"],
                                                          project=entry["project"], resolver=resolver)
        row = canonical if canonical is not None else chosen["row"]
        rb = row.get("rolled_back")
        refs = sorted((o["ref"] for o in entry["list"]), key=lambda r: (r["path"], r["line"]))
        u = {
            "schema": SCHEMA, "unit_id": uid, "identity": entry["identity"],
            "content_digest": _digest(canonical) if canonical is not None else None,
            "lineage": {"design": entry["identity"]["design"], "platform": entry["identity"]["platform"],
                        "project": entry["project"], "session": entry["identity"]["session"],
                        "seed_origin": sorted({str(o["row"].get("seed")) for o in entry["list"] if o["row"].get("seed")}),
                        "evidence_tier": row.get("evidence_tier")},
            "raw_row": canonical, "raw_variants": [o["row"] for o in {o["digest"]: o for o in entry["list"]}.values()],
            "annotations": annotations, "capture_context": chosen["ctx"],
            "action": {"strategy": row.get("strategy"), "origin": _origin(row), "config_delta": row.get("config_delta"),
                       "config_before": row.get("config_before"), "from_stage": row.get("from_stage")},
            "verification": {"check": row.get("check"), "before": row.get("before"), "after": row.get("after"),
                             "after_status": row.get("after_status"), "global_regressions": row.get("global_regressions"),
                             "rerun_failure": row.get("rerun_failure")},
            "outcome": {"verdict_as_logged": row.get("verdict"), "canonical": chosen["outcome"],
                        "verified": chosen["verified"], "new_checklist_status": "UNKNOWN"},
            "rollback": {"rolled_back": {True: "true", False: "false"}.get(rb, "UNKNOWN"),
                         "reason": row.get("rollback_reason") if rb is True else None,
                         "recovery_evidence": chosen["recovery"], "recovery_applied": None},
            "cost": {"wall_s": row.get("wall_s"), "flows": None, "cpu_s": None, "llm_requests": None,
                     "llm_tokens": None},
            "provenance": {"sources": refs, "toolchain": "UNKNOWN", "code_version": "UNKNOWN"},
            "context": {"dialogue": "missing"},
            "conflict": conflict,
            "source_eligible": canonical is not None,
        }
        u["text_view"] = _text_view(u) if canonical is not None else None
        units.append(u)
        log["units"] += 1
        log["copies_merged"] += len(refs) - 1
        if conflict:
            log["conflicts_" + conflict["kind"]] += 1
        for f, how in (annotations.get("situation_provenance") or {}).items():
            if (canonical or {}).get("situation", {}).get(f) == UNKNOWN:
                log["situation_unknown_fields"]["%s:%s" % (f, how.split(":")[0])] += 1
    log["situation_unknown_fields"] = dict(log["situation_unknown_fields"])
    return units, log


# -- rollback-status restorations (user decision D-R1) --------------------------------------------------------

def apply_restorations(units: list[dict], reviews: list[dict]) -> dict:
    """Attach the item-by-item restoration reviews (``tehm.restoration``) to the units. A RESTORED unit's
    canonical row gets ``rolled_back: false`` plus ``rollback_restoration`` (the review's digest), so its capture
    carries the marker and the link; ``rollback.rolled_back`` keeps the status AS RECORDED, the raw variants are
    untouched, and nothing is upgraded to a new acceptance checklist. Returns counts."""
    by_id = {r["unit_id"]: r for r in reviews}
    n = collections.Counter()
    for u in units:
        r = by_id.get(u["unit_id"])
        if r is None:
            continue
        digest = _digest(r)
        u["rollback"]["recovery_applied"] = {"review_digest": digest, "kind": r["kind"], "restored": r["restored"],
                                             "status_for_main": r["restored_status"],
                                             "final_retained_fix": r.get("final_retained_fix"),
                                             "scope": r.get("scope")}
        n["reviewed"] += 1
        if r.get("dialogue"):                 # the original LLM exchange, linked by content (the edit it proposed)
            u["context"] = {"dialogue": "present", "files": r["dialogue"]}
            n["dialogue_linked"] += 1
        if r["restored"] and u["raw_row"] is not None and u["rollback"]["rolled_back"] == "UNKNOWN":
            u["raw_row"] = {**u["raw_row"], "rolled_back": False, "rollback_restoration": digest}
            u["content_digest"] = _digest(u["raw_row"])
            u["text_view"] = _text_view(u) + " Rollback status restored by review %s (%s)." % (digest[:19], r["kind"])
            n["restored"] += 1
    return dict(n)


# -- source isolation (E5) ---------------------------------------------------------------------------------------

def check_isolation(units: list[dict], split: dict) -> dict:
    """Verify a split manifest against the units (protocol v1 section 4: split by upstream IP family).

    ``split``: {"family_of_design": {design: family}, "SOURCE": [families], "DEV": [...], "PROBE": [...],
    "TEST": [...], "exposed_in_development": [families]}. Errors: a unit whose design has no family; a SOURCE
    unit whose family is in PROBE/TEST; a family both in PROBE/TEST and exposed in development. DEV/SOURCE
    overlap is allowed but disclosed."""
    fam_of = split.get("family_of_design") or {}
    held_out = set(split.get("PROBE") or []) | set(split.get("TEST") or [])
    source, dev = set(split.get("SOURCE") or []), set(split.get("DEV") or [])
    exposed = set(split.get("exposed_in_development") or [])
    errors, per_family = [], collections.Counter()
    for u in units:
        fam = fam_of.get(u["identity"]["design"])
        if fam is None:
            errors.append(("design_without_family", u["identity"]["design"]))
            continue
        per_family[fam] += 1
        if fam in source and fam in held_out:
            errors.append(("source_unit_in_held_out_family", fam))
    errors += [("held_out_family_exposed_in_development", f) for f in sorted(held_out & exposed)]
    return {"ok": not errors, "errors": sorted(set(errors)), "units_per_family": dict(per_family),
            "disclosed_dev_source_overlap": sorted(dev & source)}


# -- write / read ---------------------------------------------------------------------------------------------

def write_export(units: list[dict], out_dir: Path, *, dedup_log: dict, meta: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = "".join(_canonical(u) + "\n" for u in units)
    (out_dir / "source_export.jsonl").write_text(data)
    manifest = {"schema": SCHEMA, "units": len(units), "unit_ids": [u["unit_id"] for u in units],
                "export_sha256": hashlib.sha256(data.encode()).hexdigest(), "dedup_log": dedup_log, **(meta or {})}
    (out_dir / "source_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def read_export(out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / "source_manifest.json").read_text())
    data = (out_dir / "source_export.jsonl").read_text()
    if hashlib.sha256(data.encode()).hexdigest() != manifest["export_sha256"]:
        raise ValueError("source_export.jsonl does not match its manifest digest")
    return [json.loads(line) for line in data.splitlines() if line.strip()]


# -- re-import into a TEHM store ------------------------------------------------------------------------------

def unit_record(unit: dict):
    """The ExecutionRecord of one unit, built from the unit alone (its capture context), so the same unit
    yields the same record whatever batch it is imported in."""
    from tehm.adapters.r2g_evidence import _iter_record
    ident, ctx = unit["identity"], unit["capture_context"]
    record = _iter_record(project=Path("/export") / str(ident["design"]),
                        config={"DESIGN_NAME": ident["design"], "PLATFORM": ident["platform"]}, reports={},
                        row=unit["raw_row"], prev_cumulative=ctx["prev_cumulative"], design_name=ident["design"],
                        platform=ident["platform"], session_id=ident["session"], step_index=ctx["step_index"],
                        is_terminal=ctx["is_terminal"], all_verdicts=ctx["session_verdicts"])
    if "shared_label" in unit:
        from tehm.shared_import import apply_shared_label
        return apply_shared_label(record, unit)
    return record


def import_units(conn, store, units: list[dict], *, allow_ineligible: bool = False):
    """Capture units into a TEHM store in ONE batch (all or nothing; re-import adds nothing; a conflicting
    unit raises CaptureConflict). Units are imported in canonical order (design, platform, session, step).
    Units excluded from SOURCE are refused unless ``allow_ineligible`` (DEV / sensitivity only)."""
    from tehm.adapters.r2g_evidence import _capture_batch
    bad = [u["unit_id"] for u in units if not u["source_eligible"]]
    if bad and not allow_ineligible:
        raise ValueError("units excluded from SOURCE (measured conflicts): %s" % bad[:5])
    order = sorted(units, key=lambda u: (str(u["identity"]["design"]), str(u["identity"]["platform"]),
                                         str(u["identity"]["session"]), u["capture_context"]["step_index"]))
    return _capture_batch(conn, store, [unit_record(u) for u in order])
