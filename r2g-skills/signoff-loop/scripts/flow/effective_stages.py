#!/usr/bin/env python3
"""The ONE effective-ORFS-stage resolver (RMD3-P1-01 / P1-HO-01,
failure-patterns.md #58).

Why: after a digest-verified FROM_STAGE resume (e.g. density_relief re-run from
floorplan), the def-graph FLOW gate resolved the execution ORFS-complete by
verifying recorded parent lineage, while extract_ppa/ingest_run classified the
SAME execution `partial` from the local stage_log alone — so graph gating, PPA
metadata, diagnostics, and learner statistics disagreed about whether one flow
completed (reproduced on the fixed pilot's sky130hs SHA-256 AND the held-out
sky130hs AES: it is structural, not design-specific).

This module is the single, versioned resolver every consumer calls:

  * def-graph/scripts/flow/signoff_gate.py  — imports it via the sibling-skill
    path (same reachability pattern as its stage-contract import) and keeps its
    own inline port ONLY as a standalone-deployment fallback; an equivalence
    test pins the two to the same verdicts.
  * scripts/extract/extract_ppa.py          — upgrades a lineage-complete
    resume from `partial` to `complete` in ppa.json, recording the evidence.
  * knowledge/ingest_run.py (+ repair_run_status.py through it) — same upgrade
    for runs.orfs_status, so the LEARNING gate agrees with FLOW.

Doctrine (RMD2-P0-02): recorded provenance is NEVER trusted — every inherited
stage is re-verified from bytes at resolution time (valid sha256, existing
same-identity parent, matching parent manifest digest, acyclic chain, preserved
artifact bytes re-hashed). Null/tampered/foreign/cyclic/ambiguous lineage is a
hard violation — never silently downgraded to reconstruction. Local-only stage
history stays available separately (`local_stages`) for audit.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os

STAGE_RESOLVER_VERSION = 1

# The six canonical ORFS stages a COMPLETE physical-implementation generation
# must account for — either in its own ledger or through verified parent lineage.
CANONICAL_STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")


def _stage_contract():
    """stage→artifact from the recorder-side contract (stage_artifacts.py, same
    dir); pinned fallback mirrors it for exotic import contexts."""
    here = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
    cand = os.path.join(here, "stage_artifacts.py")
    if os.path.isfile(cand):
        try:
            spec = importlib.util.spec_from_file_location("r2g_stage_artifacts", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.STAGE_ARTIFACT, mod.STAGE_CONTRACT_VERSION
        except Exception:
            pass
    return ({"synth": "1_synth.odb", "floorplan": "2_floorplan.odb",
             "place": "3_place.odb", "cts": "4_cts.odb",
             "route": "5_route.odb", "finish": "6_final.odb"}, 2)


STAGE_ARTIFACT, STAGE_CONTRACT_VERSION = _stage_contract()


def norm_stage_status(v) -> str | None:
    """Normalize a stage_log.jsonl `status` to 'pass'/'fail'/None — the ONE
    normalizer (run_orfs.sh records int exit codes; legacy writers strings;
    bool before int: it is an int subclass). Mirrors ingest_run/extract_ppa."""
    if isinstance(v, bool):
        return "pass" if v else "fail"
    if isinstance(v, (int, float)):
        return "pass" if int(v) == 0 else "fail"
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("pass", "ok", "done", "success", "passed", "0"):
            return "pass"
        if s in ("fail", "failed", "error"):
            return "fail"
    return None


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def local_stage_statuses(run_dir) -> dict | None:
    """{stage: raw status} from a run dir's stage_log.jsonl, or None when absent.
    This is the LOCAL-ONLY history (kept separately for audit — RMD3-P1-01 §5)."""
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


def _load_stage_manifest(run_dir):
    """{stage: last row} from a run's stage_artifact_manifest.jsonl ({} legacy)."""
    rows = {}
    path = os.path.join(run_dir, "stage_artifact_manifest.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("stage"):
                    rows[str(rec["stage"])] = rec
    except OSError:
        return {}
    return rows


def _run_identity(run_dir):
    """(design, platform, flow_variant) from run-meta.json / resume_meta.json."""
    meta = _load_json(os.path.join(run_dir, "run-meta.json")) or {}
    resume = _load_json(os.path.join(run_dir, "resume_meta.json")) or {}
    return (meta.get("design_name") or resume.get("design"),
            meta.get("platform") or resume.get("platform"),
            meta.get("flow_variant") or resume.get("flow_variant"))


def _artifact_in_run(run_dir, artifact):
    for sub in ("results", "final"):
        p = os.path.join(run_dir, sub, artifact)
        if os.path.isfile(p):
            return p
    return None


def verify_recorded_entry(backend, run_dir, self_run, stage, rec, self_identity):
    """Independently verify ONE recorded parent_lineage entry (RMD2-P0-02 §5.4).

    Returns (entry_dict, None) on success or (None, violation_string). Trusts
    NOTHING the recorder wrote: artifact name checked against the canonical
    contract, digest must be a real sha256, parent must exist with a successful
    matching stage record and the same design/platform/variant, the chain must
    be acyclic, and the preserved artifact bytes must still hash to the digest."""
    artifact = rec.get("artifact")
    want_art = STAGE_ARTIFACT.get(stage)
    if artifact != want_art:
        return None, (f"{stage}: recorded artifact {artifact!r} is not the canonical "
                      f"{want_art!r} (stage contract v{STAGE_CONTRACT_VERSION}) — "
                      "recorded by a defective or foreign contract")
    sha = rec.get("sha256")
    if not _is_sha256(sha):
        return None, (f"{stage}: recorded sha256={sha!r} is not a valid digest — "
                      "digest-incomplete lineage must not gate-pass (RMD2-P0-02)")
    parent = rec.get("parent_run")
    if (not parent or not isinstance(parent, str) or os.sep in parent
            or ".." in parent or not parent.startswith("RUN_")):
        return None, f"{stage}: recorded parent_run={parent!r} is not a valid sibling run tag"
    if parent == self_run:
        return None, f"{stage}: parent_run is the run itself — lineage cycle"
    parent_dir = os.path.join(backend, parent)
    if not os.path.isdir(parent_dir):
        return None, f"{stage}: parent run {parent} does not exist under {backend}"
    seen, cur = {self_run}, parent
    for _ in range(32):
        if cur in seen:
            return None, f"{stage}: lineage cycle through {cur}"
        seen.add(cur)
        nxt = ((_load_json(os.path.join(backend, cur, "resume_meta.json")) or {})
               .get("parent_lineage") or {}).get(stage, {}).get("parent_run")
        if not nxt:
            break
        cur = nxt
    else:
        return None, f"{stage}: parent chain exceeds 32 hops — ambiguous lineage"
    pid = _run_identity(parent_dir)
    for label, mine, theirs in zip(("design", "platform", "flow_variant"),
                                   self_identity, pid):
        if mine and theirs and mine != theirs:
            return None, (f"{stage}: parent {parent} is a DIFFERENT {label} "
                          f"({theirs!r} != {mine!r}) — foreign lineage rejected")
    pmani = _load_stage_manifest(parent_dir)
    prow = pmani.get(stage) or {}
    verified = False
    if _is_sha256(prow.get("sha256")):
        if prow["sha256"] != sha:
            return None, (f"{stage}: parent {parent} recorded a DIFFERENT digest for "
                          f"its {want_art} ({prow['sha256'][:12]}… != {sha[:12]}…)")
        verified = True
    else:
        pstages = local_stage_statuses(parent_dir) or {}
        if norm_stage_status(pstages.get(stage)) != "pass":
            return None, (f"{stage}: parent {parent} has neither a stage-manifest "
                          "digest nor a clean ledger row for this stage")
    apath = _artifact_in_run(run_dir, want_art)
    if not apath:
        return None, (f"{stage}: consumed artifact {want_art} is not preserved under "
                      "the run dir (results/ or final/) — bytes unverifiable, "
                      "fail closed (RMD2-P0-02)")
    actual = _sha256_file(apath)
    if actual != sha:
        return None, (f"{stage}: preserved artifact {want_art} hashes "
                      f"{(actual or 'unreadable')[:12]}… but lineage recorded "
                      f"{sha[:12]}… — the reused bytes were mutated after recording")
    entry = {"source": "recorded" if verified else "recorded_legacy_parent",
             "parent_run": parent, "sha256": sha, "artifact": want_art,
             "verified": verified}
    return entry, None


def resolve_lineage(run_dir, missing):
    """Attribute stages absent from this run's own ledger to earlier runs.

    Sources, strongest first: 'recorded' (resume_meta.json parent_lineage,
    independently verified), 'recorded_legacy_parent' (verified recording whose
    parent predates the stage manifest), 'reconstructed' (newest sibling RUN
    whose own ledger shows a clean row). A MALFORMED recording is a hard
    VIOLATION — never silently downgraded to reconstruction.
    Returns ({stage: entry}, [unresolved stages], [violations])."""
    meta = _load_json(os.path.join(run_dir, "resume_meta.json"))
    recorded = (meta or {}).get("parent_lineage") or {}
    backend = os.path.dirname(os.path.realpath(run_dir))
    self_run = os.path.basename(os.path.realpath(run_dir))
    self_identity = _run_identity(run_dir)
    try:
        siblings = sorted(
            (d for d in os.listdir(backend)
             if d.startswith("RUN_") and d != self_run
             and os.path.isdir(os.path.join(backend, d))),
            key=lambda d: os.path.getmtime(os.path.join(backend, d)), reverse=True)
    except OSError:
        siblings = []
    lineage, unresolved, violations = {}, [], []
    for stage in missing:
        rec = recorded.get(stage) or {}
        if rec:
            if rec.get("source") == "legacy_stage_log" and not rec.get("sha256"):
                pass          # recorder could not attribute (legacy corpus)
            else:
                entry, violation = verify_recorded_entry(
                    backend, run_dir, self_run, stage, rec, self_identity)
                if violation:
                    violations.append(violation)
                    continue
                lineage[stage] = entry
                continue
        for sib in siblings:
            pstages = local_stage_statuses(os.path.join(backend, sib)) or {}
            if norm_stage_status(pstages.get(stage)) == "pass":
                lineage[stage] = {"source": "reconstructed", "parent_run": sib}
                break
        else:
            unresolved.append(stage)
    return lineage, unresolved, violations


def lineage_root_digest(lineage: dict) -> str:
    return hashlib.sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest()


def resolve(run_dir) -> dict:
    """Effective ORFS completion for a run dir, merging the local ledger with
    digest-verified parent lineage. THE shared verdict (RMD3-P1-01):

      status    complete | partial | fail | unknown
      + machine-readable provenance: local_stages (audit), lineage,
        lineage_quality, lineage_violations, lineage_root_digest, unresolved,
        fail_stage / last_stage, resolver_version, stage_contract_version.
    """
    out = {"resolver_version": STAGE_RESOLVER_VERSION,
           "stage_contract_version": STAGE_CONTRACT_VERSION}
    stages = local_stage_statuses(run_dir) if run_dir else None
    if stages is None:
        out.update(status="unknown", detail="no stage_log.jsonl", local_stages=None)
        return out
    out["local_stages"] = stages
    norm = {s: norm_stage_status(st) for s, st in stages.items()}
    bad = [s for s, st in norm.items() if st == "fail"]
    passed = {s for s, st in norm.items() if st == "pass"}
    ordered = [s for s in CANONICAL_STAGES if s in passed]
    out["last_stage"] = ordered[-1] if ordered else None
    if bad:
        fail_stage = next((s for s in CANONICAL_STAGES if s in bad), bad[0])
        out.update(status="fail", fail_stage=fail_stage,
                   detail=f"stage(s) failed: {sorted(bad)}")
        return out
    missing = [s for s in CANONICAL_STAGES if s not in passed]
    if not missing:
        out.update(status="complete", fail_stage=None, lineage={},
                   lineage_quality="local", detail="all six stages in local ledger")
        return out
    lineage, unresolved, violations = resolve_lineage(run_dir, missing)
    out.update(lineage=lineage, lineage_violations=violations,
               unresolved=unresolved,
               lineage_root_digest=lineage_root_digest(lineage))
    if violations:
        out.update(status="partial",
                   fail_stage=next((s for s in CANONICAL_STAGES if s not in passed), None),
                   detail="repair/resume generation with BROKEN recorded lineage "
                          "(RMD2-P0-02): " + "; ".join(violations))
        return out
    if unresolved:
        out.update(status="partial",
                   fail_stage=next((s for s in CANONICAL_STAGES if s not in passed), None),
                   detail=f"unattributed stage(s) {unresolved} — a clean 'finish' "
                          "row alone is not completion (pilot P0-4)")
        return out
    quality = ("recorded"
               if all(v.get("source") == "recorded" for v in lineage.values())
               else "reconstructed")
    out.update(status="complete", fail_stage=None, lineage_quality=quality,
               detail=f"repair/resume generation; reused stages attributed via "
                      f"{quality} parent lineage")
    return out


def effective_upgrade(run_dir, local_status: str, flow_scope: str = "full") -> dict | None:
    """Consumer helper (extract_ppa / ingest_run): given a LOCAL classification,
    return the effective-resolution blob when (and only when) verified lineage
    upgrades a no-failure `partial` to `complete`. Returns None when no upgrade
    applies (local fail/complete/unknown, synth-only scope, or lineage does not
    complete the six stages) — consumers then keep their local verdict, and a
    violation/unresolved lineage therefore FAILS CLOSED to `partial`."""
    if local_status != "partial" or flow_scope == "synth_only" or not run_dir:
        return None
    try:
        res = resolve(run_dir)
    except Exception:
        return None       # resolver crash must never break extraction/ingest
    if res.get("status") != "complete" or not res.get("lineage"):
        return None
    return res
