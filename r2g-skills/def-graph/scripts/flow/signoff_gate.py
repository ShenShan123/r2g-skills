#!/usr/bin/env python3
"""Signoff gate for dataset construction: a 6_final.def alone is NOT sign-off.

DRC/LVS run in a separate post-finish step, route/antenna residuals survive a
"completed" flow, and an aborted ORFS can leave a plausible DEF behind — so the
dataset stages must not build just because the DEF exists (failure-patterns.md
"Dataset-Extraction Silent-Value Defects" #34). This gate reads the project's
signoff artifacts and decides whether a dataset may be built from this run:

  required (block in enforce mode when dirty OR unverifiable — fail-closed):
    reports/drc.json                status in {clean, clean_beol}
    reports/lvs.json                status in {clean, skipped}
    <run_dir>/stage_log.jsonl       'finish' stage recorded with status 0
                                    (fallback: run-meta.json make_status == 0)
    reports/route.json | <run_dir>/**/5_route_drc.rpt
                                    residual route violations == 0
                                    (unknown = caveat, not a block: a clean full
                                    DRC deck already covers routed geometry)
  recorded per-metric, never a new blocker (additive visibility — codex #5):
    antenna                         its OWN clean/fail/nonconverged/not_covered/
                                    unknown dimension, decoupled from routing-DRC:
                                    reports/antenna_nonconverged.json (the fix
                                    loop gave up) or antenna-named drc.json
                                    categories. A routing-clean-but-antenna-dirty
                                    design is thus visible in the manifest.
  advisory (recorded, never blocks — negative slack is a valid training label):
    reports/ppa.json summary.timing.setup_wns | reports/timing_check.json tier

Always writes reports/signoff_gate.json (atomic tmp+rename); build_graphs.py
embeds it in graph_manifest.json as `signoff_health`. Exit code:
  0  proceed  (verdict pass/pass_with_caveats, or mode warn/off;
              mode strict: exact 'pass' ONLY)
  3  blocked  (mode enforce and a required check failed, or mode strict and
              the verdict is anything but exact 'pass')

Fail-closed on MISSING drc/lvs reports in enforce mode: the verifier's old
vacuous pass (no report -> no check -> "clean") is the exact trap this replaces.

Modes / tiers (pilot P0-1, 2026-07-21): `enforce` (default) blocks on dirty or
unverifiable signoff but lets pass_with_caveats build — such a dataset is a
RESEARCH-tier artifact (build_graphs.py stamps dataset_tier accordingly and it
may never enter a clean index). `strict` is the V1 clean tier: only the exact
verdict 'pass' (no blockers AND no caveats — full-deck DRC clean, LVS clean,
six-stage lineage, route clean, antenna clean, timing met, reports bound) may
build. Select with R2G_SIGNOFF_GATE=strict.
Overrides: R2G_SIGNOFF_GATE=warn builds anyway with the reasons recorded;
--def-overridden (R2G_DEF/R2G_ODB set) downgrades to warn — an explicit operator
override is a deliberate, recorded decision, e.g. the no-backend verifier flows.
"""
import argparse
import glob
import hashlib
import json
import os
import sys

# Statuses the signoff step itself treats as acceptable (fix_signoff.sh's
# clean_states) — but the gate is stricter: `skipped` is acceptable only for
# LVS (portless designs / platforms without a deck record an EXPLICIT skip),
# never for DRC, and a MISSING report is not a skip.
DRC_OK = {"clean", "clean_beol"}
LVS_OK = {"clean", "skipped"}
PROCEED = {"pass", "pass_with_caveats"}


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _check_drc(reports_dir):
    j = _load_json(os.path.join(reports_dir, "drc.json"))
    if j is None:
        return {"status": "missing", "detail": "reports/drc.json not found — DRC never ran (or ran elsewhere)"}
    st = str(j.get("status", "unknown"))
    out = {"status": st, "violations": j.get("total_violations")}
    if st not in DRC_OK:
        out["detail"] = f"drc status={st!r} violations={j.get('total_violations')}"
    elif st == "clean_beol":
        out["detail"] = "BEOL-only DRC: metal clean, FEOL/antenna not covered"
    return out


def _check_lvs(reports_dir):
    j = _load_json(os.path.join(reports_dir, "lvs.json"))
    if j is None:
        return {"status": "missing", "detail": "reports/lvs.json not found — LVS never ran (or ran elsewhere)"}
    st = str(j.get("status", "unknown"))
    out = {"status": st, "mismatch_count": j.get("mismatch_count")}
    if st not in LVS_OK:
        out["detail"] = f"lvs status={st!r} mismatch_count={j.get('mismatch_count')}"
    elif st == "skipped":
        out["detail"] = "LVS explicitly skipped by the signoff step (portless design / no deck)"
    return out


# The six canonical ORFS stages a COMPLETE physical-implementation generation
# must account for — either in its own ledger or through a reconstructable
# parent lineage (pilot P0-4, 2026-07-21).
CANONICAL_STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")


def _stage_statuses(run_dir):
    """{stage: status} from a run dir's stage_log.jsonl, or None when absent."""
    slog = os.path.join(run_dir, "stage_log.jsonl")
    if not os.path.isfile(slog):
        return None
    stages = {}
    try:
        with open(slog, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                stages[str(rec.get("stage", ""))] = rec.get("status")
    except OSError:
        return None
    return stages


def _resolve_lineage(run_dir, missing):
    """Attribute stages absent from this run's own ledger to earlier runs.

    A repair/resume generation (run_orfs.sh FROM_STAGE) reruns only the fixed
    stages, so its RUN dir ledger holds e.g. only route+finish while synth..cts
    were consumed from an earlier run's artifacts. Sources, strongest first:
      * 'recorded'      — resume_meta.json parent_lineage (written at resume time
                          with the consumed artifact's sha256; 2026-07-21)
      * 'reconstructed' — newest sibling RUN whose own ledger shows a clean row
                          for the stage (pre-P0-4 resumes carry no recording)
    Returns ({stage: {source, parent_run, sha256}}, [unresolved stages])."""
    meta = _load_json(os.path.join(run_dir, "resume_meta.json"))
    recorded = (meta or {}).get("parent_lineage") or {}
    backend = os.path.dirname(os.path.realpath(run_dir))
    self_run = os.path.basename(os.path.realpath(run_dir))
    try:
        siblings = sorted(
            (d for d in os.listdir(backend)
             if d.startswith("RUN_") and d != self_run
             and os.path.isdir(os.path.join(backend, d))),
            key=lambda d: os.path.getmtime(os.path.join(backend, d)), reverse=True)
    except OSError:
        siblings = []
    lineage, unresolved = {}, []
    for stage in missing:
        rec = recorded.get(stage) or {}
        parent = rec.get("parent_run")
        if parent and os.path.isdir(os.path.join(backend, parent)):
            pstages = _stage_statuses(os.path.join(backend, parent)) or {}
            if pstages.get(stage) in (0, "0"):
                lineage[stage] = {"source": "recorded", "parent_run": parent,
                                  "sha256": rec.get("sha256")}
                continue
        for sib in siblings:
            pstages = _stage_statuses(os.path.join(backend, sib)) or {}
            if pstages.get(stage) in (0, "0"):
                lineage[stage] = {"source": "reconstructed", "parent_run": sib}
                break
        else:
            unresolved.append(stage)
    return lineage, unresolved


def _check_orfs(run_dir):
    """ORFS completion from the run the DEF came from: stage_log.jsonl is the
    authoritative record (one JSON line per stage, written by run_orfs.sh);
    run-meta.json make_status is the coarser fallback.

    'complete' requires a RECONSTRUCTABLE SIX-STAGE LINEAGE, not merely a clean
    'finish' row (pilot P0-4): a repair-only generation (route+finish rerun)
    used to read complete here although synth..cts were absent from its ledger
    and nothing proved which upstream artifacts it consumed."""
    if not run_dir:
        return {"status": "unknown", "detail": "no backend run dir (DEF overridden or externally collected)"}
    stages = _stage_statuses(run_dir)
    if stages is not None:
        bad = {s: st for s, st in stages.items() if st not in (0, "0")}
        if bad:
            return {"status": "fail", "detail": f"stage(s) failed: {bad}", "stages": stages}
        if stages.get("finish") not in (0, "0"):
            if stages:
                return {"status": "incomplete",
                        "detail": f"no clean 'finish' stage in stage_log.jsonl (saw: {sorted(stages)})",
                        "stages": stages}
        else:
            clean = {s for s, st in stages.items() if st in (0, "0")}
            missing = [s for s in CANONICAL_STAGES if s not in clean]
            if not missing:
                return {"status": "complete", "stages": stages}
            lineage, unresolved = _resolve_lineage(run_dir, missing)
            if not unresolved:
                quality = ("recorded"
                           if all(v.get("source") == "recorded" for v in lineage.values())
                           else "reconstructed")
                return {"status": "complete", "stages": stages,
                        "lineage": lineage, "lineage_quality": quality,
                        "detail": f"repair/resume generation; reused stages attributed "
                                  f"via {quality} parent lineage"}
            return {"status": "incomplete", "stages": stages,
                    "lineage": lineage,
                    "detail": "repair-only generation without a reconstructable "
                              f"six-stage lineage: unattributed stage(s) {unresolved} "
                              "(pilot P0-4 — a clean 'finish' row alone is not completion)"}
    meta = _load_json(os.path.join(run_dir, "run-meta.json"))
    if meta is not None and "make_status" in meta:
        ms = meta.get("make_status")
        if ms in (0, "0"):
            return {"status": "complete", "detail": "run-meta.json make_status=0 (no stage_log.jsonl)"}
        return {"status": "fail", "detail": f"run-meta.json make_status={ms}"}
    return {"status": "unknown",
            "detail": f"no stage_log.jsonl / run-meta.json make_status under {run_dir}"}


def _check_route(reports_dir, run_dir):
    """Residual route/antenna violations. Prefer the extracted reports/route.json;
    fall back to counting markers in the run's 5_route_drc.rpt. Unknown when
    neither exists — recorded as a caveat, not a block (a clean full DRC deck
    already covers routed geometry)."""
    j = _load_json(os.path.join(reports_dir, "route.json"))
    if j is not None:
        tv = j.get("total_violations")
        st = str(j.get("status", "unknown"))
        # Gate on the COUNT, not the status string: a route.json carrying
        # status='clean' but total_violations>0 (foreign writer) must NOT read
        # clean via short-circuit (failure-patterns.md #38). And a genuine
        # status='unknown' (route stage never reached) is 'unknown' (caveat),
        # not 'dirty' (a spurious blocker).
        try:
            tv_num = None if tv is None else int(tv)
        except (TypeError, ValueError):
            tv_num = None
        if tv_num == 0:
            return {"status": "clean", "violations": 0}
        if tv_num is not None and tv_num > 0:
            return {"status": "dirty", "violations": tv_num,
                    "detail": f"route.json status={st!r} total_violations={tv_num}"}
        # tv unknown/non-numeric: trust an explicit 'clean' only, map 'unknown'
        # to unknown, never silently promote another status to clean.
        if st == "clean":
            return {"status": "clean", "violations": 0}
        if st == "unknown":
            return {"status": "unknown",
                    "detail": "route.json status=unknown (route stage not reached / not parsed)"}
        return {"status": "dirty", "violations": tv,
                "detail": f"route.json status={st!r} total_violations={tv}"}
    if run_dir:
        rpts = sorted(glob.glob(os.path.join(run_dir, "**", "5_route_drc.rpt"),
                                recursive=True))
        if rpts:
            try:
                with open(rpts[-1], errors="replace", encoding="utf-8") as f:
                    n = sum(1 for line in f if "violation type" in line.lower())
            except OSError:
                n = -1
            if n == 0:
                return {"status": "clean", "violations": 0, "source": rpts[-1]}
            return {"status": "dirty", "violations": n,
                    "detail": f"{n} residual marker(s) in {rpts[-1]}", "source": rpts[-1]}
    return {"status": "unknown",
            "detail": "no reports/route.json and no 5_route_drc.rpt in the run dir"}


def _check_antenna(reports_dir):
    """Antenna tracked as its OWN pass/fail/unknown dimension, decoupled from
    routing-DRC and full-deck DRC (failure-patterns.md #38 / codex #5). Routing
    DRC (shorts/spacing) and antenna are separate manufacturing metrics — a
    layout can be routing-clean while antenna-dirty — so a dataset consumer must
    be able to filter on antenna alone. Sources, in order:

      reports/antenna_nonconverged.json  -> 'nonconverged' (the fix loop gave up
                                            on residual antenna, #36 — the
                                            suggestion's exact stall example)
      reports/drc.json categories        -> sum counts of antenna-named classes

    Status: nonconverged | fail | clean | not_covered (clean_beol disables the
    ANTENNA rule group) | unknown (drc.json missing/stale, or a fail with no
    per-category breakdown so antenna is not separable). NEVER a hard blocker
    here — a full-deck antenna failure already blocks via `drc`; this dimension
    is additive visibility so it can ride the manifest as a recorded risk."""
    marker = _load_json(os.path.join(reports_dir, "antenna_nonconverged.json"))
    if isinstance(marker, dict):
        return {"status": "nonconverged",
                "residual_count": marker.get("residual_count"),
                "strategies_tried": marker.get("strategies_tried"),
                "detail": "antenna repair non-converged (reports/antenna_nonconverged.json)"}
    j = _load_json(os.path.join(reports_dir, "drc.json"))
    if j is None:
        return {"status": "unknown", "detail": "no reports/drc.json — antenna not separately verified"}
    st = str(j.get("status", "unknown"))
    if st == "clean_beol":
        return {"status": "not_covered",
                "detail": "BEOL-only DRC: ANTENNA rule group disabled — antenna NOT verified"}
    cats = j.get("categories") or {}
    ant = sum((c or {}).get("count", 0) or 0
              for k, c in cats.items() if "antenna" in str(k).lower())
    if ant > 0:
        return {"status": "fail", "violations": ant,
                "detail": f"{ant} antenna-class DRC violation(s)"}
    if st in DRC_OK:
        return {"status": "clean", "violations": 0}
    if st == "fail" and cats:
        # Full deck ran & categorized; the failure is non-antenna classes -> the
        # antenna metric itself is clean (this IS the decoupling the fix targets).
        return {"status": "clean", "violations": 0,
                "detail": "DRC failed on non-antenna classes; antenna clean"}
    return {"status": "unknown",
            "detail": f"drc status={st!r} with no per-category breakdown — antenna not separable"}


def _check_timing(reports_dir):
    """Advisory only: negative slack is a legitimate training label, so timing is
    recorded for downstream filtering, never a block."""
    ppa = _load_json(os.path.join(reports_dir, "ppa.json"))
    if ppa is not None:
        wns = ((ppa.get("summary") or {}).get("timing") or {}).get("setup_wns")
        if wns is not None:
            try:
                met = float(wns) >= 0.0
            except (TypeError, ValueError):
                met = None
            return {"status": ("met" if met else "violated") if met is not None else "unknown",
                    "setup_wns": wns, "source": "ppa.json"}
    tc = _load_json(os.path.join(reports_dir, "timing_check.json"))
    if tc is not None and tc.get("tier"):
        tier = str(tc["tier"])
        return {"status": "met" if tier in ("clean", "minor") else "violated",
                "tier": tier, "source": "timing_check.json"}
    return {"status": "unknown", "detail": "no reports/ppa.json timing or timing_check.json"}


def _def_fingerprint(def_path):
    """Recomputable identity for the DEF being graphed: path + size + mtime + a full
    sha256 content digest. The digest binds the manifest to the EXACT bytes certified —
    size+mtime alone can't detect an in-place rewrite that preserves both (agent-logic
    #5, 2026-07-16) — and a single streamed hash of a tens-of-MB DEF once per stage is
    cheap. None when the DEF path is absent/unreadable; sha256=None if it stats but the
    content can't be read (keys path/size/mtime stay for the verifier)."""
    if not def_path:
        return None
    try:
        st = os.stat(def_path)
    except OSError:
        return None
    digest = None
    try:
        h = hashlib.sha256()
        with open(def_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        pass
    return {"path": os.path.realpath(def_path), "size": st.st_size,
            "mtime": int(st.st_mtime), "sha256": digest}


def _check_binding(def_path, run_dir):
    """Bind the DEF being graphed to the RUN whose signoff reports are being gated
    (P0-17, 2026-07-15). A clean report bundle from one run must not certify a layout
    artifact from ANOTHER run: the gate checks report contents independently, so
    without this a mixed bundle (run-2 DEF + run-1 reports) passes. In the normal flow
    the DEF is discovered UNDER the run dir (<run>/{final,results}/6_final.def), so the
    binding invariant is 'the DEF lives under this run dir'. A DEF outside it is UNBOUND
    (blocks in enforce). No DEF/run info -> 'unknown' caveat (e.g. an R2G_DEF override,
    which already downgrades enforce->warn, or an externally-collected DEF)."""
    if not def_path:
        return {"status": "unknown", "detail": "no DEF path supplied to the gate"}
    if not os.path.isfile(def_path):
        return {"status": "unknown", "detail": f"DEF not found: {def_path}"}
    if not run_dir or not os.path.isdir(run_dir):
        return {"status": "unknown",
                "detail": "no backend run dir (DEF overridden / externally collected)"}
    def_real = os.path.realpath(def_path)
    run_real = os.path.realpath(run_dir)
    if def_real == run_real or def_real.startswith(run_real + os.sep):
        return {"status": "bound", "def_run_dir": run_real}
    return {"status": "unbound",
            "detail": f"DEF {def_real} is NOT under the reports' run dir {run_real} — "
                      "the report bundle belongs to a DIFFERENT run than this layout"}


def _check_report_binding(reports_dir, run_dir):
    """Bind the signoff REPORTS to the run whose DEF is being graphed (P0-R7).

    `_check_binding` above answers "does this DEF belong to this run dir?" — but
    in the normal flow the DEF is DISCOVERED under the run dir, so that check is
    close to tautological. Its real blind spot is the other direction: the
    verdicts themselves are read from <project>/reports/, which is project-level
    while a project accumulates many RUN_* dirs. Two real wbuart32 runs with
    different DEF digests (R1 d6426fae…, R2 cc2da796…) let R1's clean bundle
    certify R2's layout, and the gate said pass_with_caveats.

    Each report now carries report_io's `provenance` envelope naming the run it
    judged. Rules:
      * any report naming a DIFFERENT run than the one selected -> `foreign`
        (a hard blocker: clean results from one run must never certify another);
      * no report carrying provenance, in a project with MORE THAN ONE backend
        run -> `unknown` (a recorded caveat, not a block: every pre-2026-07-20
        report is unstamped, and re-running DRC/LVS across a corpus purely to
        acquire attribution costs hours per design. They self-heal on the next
        signoff run);
      * a report attributed by the weakest source ('latest_run' — a guess, not
        the restage marker) -> `weak`, also a caveat.

    A single-run project is `bound` even with unattributed reports: there is no
    OTHER run the verdicts could have come from, so there is nothing to warn
    about. Flagging it would put every existing clean single-run design into
    pass_with_caveats — noise that would mask the caveats that mean something.
    """
    if not run_dir or not os.path.isdir(run_dir):
        return {"status": "unknown", "detail": "no backend run dir to bind reports to"}
    want = os.path.basename(os.path.realpath(run_dir))
    backend = os.path.dirname(os.path.realpath(run_dir))
    try:
        n_runs = len([d for d in os.listdir(backend) if d.startswith("RUN_")])
    except OSError:
        n_runs = 1
    seen, foreign, weak = {}, [], []
    for fn in ("drc.json", "lvs.json", "route.json", "ppa.json"):
        doc = _load_json(os.path.join(reports_dir, fn))
        if not isinstance(doc, dict):
            continue
        prov = doc.get("provenance")
        tag = (prov or {}).get("run_tag") if isinstance(prov, dict) else None
        # route.json has recorded `backend_run` since before the envelope existed;
        # honor it so pre-envelope route reports still bind.
        if not tag and isinstance(doc.get("backend_run"), str):
            tag = doc["backend_run"]
            prov = {"source": "backend_run"}
        if not tag:
            continue
        seen[fn] = tag
        if tag != want:
            foreign.append(f"{fn}={tag}")
        elif (prov or {}).get("source") == "latest_run":
            weak.append(fn)
    if foreign:
        return {"status": "foreign", "expected_run": want, "reports": seen,
                "detail": f"signoff reports belong to a DIFFERENT backend run than the "
                          f"selected layout ({want}): {', '.join(sorted(foreign))}"}
    if not seen:
        if n_runs <= 1:
            return {"status": "bound", "expected_run": want, "reports": {},
                    "detail": "reports unattributed, but the project has a single "
                              "backend run — no other run they could describe"}
        return {"status": "unknown", "expected_run": want, "runs": n_runs,
                "detail": f"no signoff report records which run it judged, and this "
                          f"project has {n_runs} backend runs (pre-P0-R7 reports); "
                          "re-run signoff to attribute them"}
    return {"status": "bound", "expected_run": want, "reports": seen,
            **({"weak": weak} if weak else {})}


def _sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _check_artifact_digest(reports_dir, run_dir, project_dir):
    """Bind the signoff reports to the EXACT LAYOUT BYTES of the selected run
    (RMD-P0-02, three-platform pilot 2026-07-22).

    Run-tag binding (`report_binding` above) proves the report names this run's
    DIRECTORY — it cannot see foreign bytes copied into the expected path. Each
    checker now records the sha256 of the GDS it actually graded (drc.json /
    lvs.json `gds_sha256`, or the provenance envelope's digest from the shared
    signoff record). Compare every recorded digest against the run's actual
    6_final.gds:

      * any recorded digest differing from the actual bytes -> `mismatch`
        (HARD block: the verdict describes a different layout);
      * an existing but unreadable backend/.r2g_signoff_run record -> `unreadable_record`
        (HARD block per the remediation plan: broken provenance must fail closed,
        not silently degrade);
      * reports present but none recording a digest -> `unrecorded` (a caveat:
        legacy evidence may still build the research tier, but the strict
        r2g_clean tier requires exact 'pass', so it can never auto-certify);
      * no run dir / no GDS to compare -> `unknown` (nothing to check).
    """
    rec_path = os.path.join(project_dir, "backend", ".r2g_signoff_run")
    if os.path.isfile(rec_path):
        rec = _load_json(rec_path)
        if not isinstance(rec, dict) or not rec.get("run_tag"):
            return {"status": "unreadable_record",
                    "detail": f"backend/.r2g_signoff_run exists but is unreadable — "
                              "provenance must fail closed (RMD-P0-02)"}
    if not run_dir or not os.path.isdir(run_dir):
        return {"status": "unknown", "detail": "no backend run dir to digest"}
    actual = None
    for sub in ("results", "final"):
        p = os.path.join(run_dir, sub, "6_final.gds")
        if os.path.isfile(p):
            actual = _sha256_file(p)
            break
    if not actual:
        return {"status": "unknown", "detail": "selected run has no readable 6_final.gds"}
    recorded, mismatched = {}, []
    for fn in ("drc.json", "lvs.json"):
        doc = _load_json(os.path.join(reports_dir, fn))
        if not isinstance(doc, dict):
            continue
        digest = doc.get("gds_sha256")
        if not digest:
            prov = doc.get("provenance")
            digest = (prov or {}).get("gds_sha256") if isinstance(prov, dict) else None
        if not digest:
            continue
        recorded[fn] = digest
        if digest != actual:
            mismatched.append(fn)
    if mismatched:
        return {"status": "mismatch", "actual_gds_sha256": actual,
                "recorded": recorded,
                "detail": "signoff report(s) record a DIFFERENT layout digest than the "
                          f"selected run's 6_final.gds: {', '.join(sorted(mismatched))} "
                          "— the verdicts grade foreign bytes (RMD-P0-02)"}
    if not recorded:
        return {"status": "unrecorded", "actual_gds_sha256": actual,
                "detail": "no signoff report records the layout digest it graded "
                          "(pre-RMD-P0-02 evidence); re-run signoff to bind it"}
    return {"status": "bound", "actual_gds_sha256": actual, "recorded": recorded}


def evaluate(project_dir, run_dir, def_path=None):
    reports_dir = os.path.join(project_dir, "reports")
    checks = {
        "drc": _check_drc(reports_dir),
        "lvs": _check_lvs(reports_dir),
        "orfs": _check_orfs(run_dir),
        "route": _check_route(reports_dir, run_dir),
        "antenna": _check_antenna(reports_dir),
        "timing": _check_timing(reports_dir),
        "binding": _check_binding(def_path, run_dir),
        "report_binding": _check_report_binding(reports_dir, run_dir),
        "artifact_digest": _check_artifact_digest(reports_dir, run_dir, project_dir),
    }
    blockers = []
    if checks["drc"]["status"] not in DRC_OK:
        blockers.append("drc")
    if checks["lvs"]["status"] not in LVS_OK:
        blockers.append("lvs")
    if checks["orfs"]["status"] not in ("complete",):
        blockers.append("orfs")
    if checks["route"]["status"] == "dirty":
        blockers.append("route")
    # A DEF that does not belong to the gated run is a HARD block: a clean report bundle
    # from another run must never certify this layout (P0-17). 'unknown' (no DEF supplied
    # / override) is a caveat, not a block.
    if checks["binding"]["status"] == "unbound":
        blockers.append("binding")
    # Reports that positively name a DIFFERENT run are a hard block (P0-R7) — this
    # is the direction _check_binding cannot see. Unattributed reports are only a
    # caveat (see _check_report_binding for why).
    if checks["report_binding"]["status"] == "foreign":
        blockers.append("report_binding")
    # A recorded layout digest that differs from the selected run's actual GDS
    # bytes, or an unreadable signoff record, is a hard block (RMD-P0-02):
    # copying foreign DEF/GDS bytes into the expected run path must not bypass
    # the gate.
    if checks["artifact_digest"]["status"] in ("mismatch", "unreadable_record"):
        blockers.append("artifact_digest")

    caveats = []
    # Only a SUPPLIED-but-unverifiable DEF is a recorded caveat; a caller that passes no
    # DEF (legacy 2-arg evaluate) makes no binding claim, so it stays a clean pass.
    if def_path and checks["binding"]["status"] == "unknown":
        caveats.append("binding=unknown")
    if checks["report_binding"]["status"] == "unknown":
        caveats.append("report_binding=unknown")
    elif checks["report_binding"].get("weak"):
        caveats.append("report_binding=weak")
    # Legacy (pre-RMD-P0-02) evidence carries no layout digest: buildable as
    # research tier, but never an exact 'pass' — the strict r2g_clean tier
    # requires digest-bound reports.
    if checks["artifact_digest"]["status"] == "unrecorded":
        caveats.append("artifact_digest=unrecorded")
    fp = _def_fingerprint(def_path)
    if fp:
        checks["binding"]["def_fingerprint"] = fp
    if checks["drc"]["status"] == "clean_beol":
        caveats.append("drc=clean_beol")
    if checks["lvs"]["status"] == "skipped":
        caveats.append("lvs=skipped")
    if checks["route"]["status"] == "unknown":
        caveats.append("route=unknown")
    # A repair/resume generation whose reused stages were only RECONSTRUCTED from
    # sibling ledgers (no recorded parent chain — pre-P0-4 resume) is complete but
    # weakly bound; record it. A 'recorded' lineage carries consumed-artifact
    # digests and is not a caveat.
    if checks["orfs"].get("lineage_quality") == "reconstructed":
        caveats.append("orfs_lineage=reconstructed")
    # Antenna as its own recorded risk (never a new blocker — a full-deck antenna
    # failure already blocks via `drc`). Anything but a proven-clean antenna is a
    # recorded caveat so a routing-clean-but-antenna-dirty design is visible.
    if checks["antenna"]["status"] != "clean":
        caveats.append(f"antenna={checks['antenna']['status']}")
    if checks["timing"]["status"] != "met":
        caveats.append(f"timing={checks['timing']['status']}")

    status = "dirty" if blockers else ("pass_with_caveats" if caveats else "pass")
    return {"status": status, "blockers": blockers, "caveats": caveats, "checks": checks}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--run-dir", default="", help="backend RUN_* dir the DEF came from")
    ap.add_argument("--def", dest="def_path", default="",
                    help="the selected 6_final.def being graphed — bound to --run-dir (P0-17)")
    ap.add_argument("--mode", default="enforce", choices=("enforce", "strict", "warn", "off"))
    ap.add_argument("--def-overridden", action="store_true",
                    help="R2G_DEF/R2G_ODB set: downgrade enforce to warn (deliberate operator override)")
    args = ap.parse_args()

    mode = args.mode
    if args.def_overridden and mode in ("enforce", "strict"):
        mode = "warn"

    if mode == "off":
        verdict = {"status": "gate_off", "mode": "off"}
    else:
        verdict = evaluate(args.project_dir, args.run_dir, args.def_path or None)
        verdict["mode"] = mode
        if args.def_overridden:
            verdict["def_overridden"] = True

    reports_dir = os.path.join(args.project_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    out = os.path.join(reports_dir, "signoff_gate.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=1)
    os.replace(tmp, out)

    if verdict["status"] in PROCEED:
        # Strict V1 tier (pilot P0-1): pass_with_caveats is NOT publishable —
        # only the exact verdict 'pass' may build the clean tier.
        if mode == "strict" and verdict["status"] != "pass":
            print("signoff gate: BLOCKED (strict tier requires exact 'pass'; "
                  f"got {verdict['status']!r}, caveats: {', '.join(verdict.get('caveats') or [])})",
                  file=sys.stderr)
            print(f"  verdict recorded in {out}", file=sys.stderr)
            return 3
        note = f" (caveats: {', '.join(verdict['caveats'])})" if verdict.get("caveats") else ""
        print(f"signoff gate: {verdict['status']}{note}", file=sys.stderr)
        return 0
    if verdict["status"] == "gate_off":
        print("signoff gate: OFF (R2G_SIGNOFF_GATE=off) — provenance unrecorded", file=sys.stderr)
        return 0
    detail = "; ".join(
        f"{k}: {verdict['checks'][k].get('detail', verdict['checks'][k]['status'])}"
        for k in verdict["blockers"])
    print(f"signoff gate: NOT SIGNED OFF — {detail}", file=sys.stderr)
    print(f"  verdict recorded in {out}", file=sys.stderr)
    if mode == "enforce":
        return 3
    print("  proceeding anyway (mode=warn) — the manifest will carry signoff_health="
          f"{verdict['status']!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
