#!/usr/bin/env python3
"""Build an evaluation-only R0/R1/R2 RTL causal-retrieval report.

The report deliberately uses a SQLite backup of the frozen v4 snapshot.  The
training transitions are already real-Icarus evidence in that snapshot; the
held-out fixtures are executed into the derived database only to construct
their query signatures.  Causal paths are searchable shadow objects, never
production rules, and no canonical source database or lifecycle status is
modified.

R0 routes by executable transformation family, R1 adds compatibility profile,
and R2 adds the observed mechanism family plus the held-out effect key.  A
controlled negative slice adds an unseen concrete module to show the causal
detail veto: metadata arms may return a path while R2 must abstain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.causal import (  # noqa: E402
    build_transition_causal_fragment, consolidate_causal_path,
)
from tehm.causal.mechanism import load_transition_facts, mechanism_signature  # noqa: E402
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.retrieval.causal_recall import retrieve_causal_paths  # noqa: E402
from tehm.rtl.rtl_evidence import build_rtl_execution_record  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from contracts import MemoryQuery  # noqa: E402


REPORT_VERSION = "tehm-causal-retrieval-eval-v3"
CAMPAIGN_ID = "live"
MATERIALIZED_AT = "2026-08-22T00:00:00+00:00"
DEFAULT_TRAINING = (
    "p3_positive_valid_ready", "p3_positive_fifo_space",
    "p3_obligation_recovery_b", "p3_reset_restore_a",
    "p3_reset_restore_c", "p3_width_correct_a", "p3_width_correct_b",
    "p3_overlap_priority_a", "p3_overlap_priority_b",
)
DEFAULT_HELDOUT = (
    "p3_obligation_recovery", "p3_reset_restore_b",
    "p3_width_correct_c", "p3_overlap_priority_c",
)


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _manifest(project: Path) -> dict:
    value = json.loads((project / "manifest.json").read_text())
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be an object: {project}")
    return value


def _lineage(project: Path) -> str:
    return str(_manifest(project).get("design") or project.name)


def _project_root() -> Path:
    return ROOT / "tests" / "fixtures" / "rtl_projects"


def _resolve_projects(names: list[str], label: str) -> list[Path]:
    result = []
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = _project_root() / path
        path = path.resolve()
        if not (path / "manifest.json").is_file():
            raise FileNotFoundError(f"{label} project manifest not found: {path}")
        result.append(path)
    lineages = [_lineage(path) for path in result]
    if len(set(lineages)) != len(lineages):
        raise ValueError(f"{label} projects must have distinct lineages")
    return result


def _source_transition(conn, project: Path) -> str:
    lineage = _lineage(project)
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
             JOIN tehm_states s ON s.state_id=t.source_state_id
             JOIN tehm_dataset_membership dm ON dm.transition_id=t.transition_id
            WHERE s.lineage_id=? AND dm.campaign_id=? AND dm.split='training'
                  AND dm.learner_eligible=1
            ORDER BY t.transition_id""", (lineage, CAMPAIGN_ID)).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"expected exactly one learner-eligible training transition for {lineage}, "
            f"got {len(rows)}")
    transition_id = str(rows[0]["transition_id"])
    facts = load_transition_facts(conn, transition_id)
    if facts.verifier.get("verdict") != "PASS":
        raise ValueError(f"training transition is not independently verified: {lineage}")
    return transition_id


def _query_plan(*, signature: dict, effect_key: str | None,
                arm: str) -> dict:
    """Construct one frozen arm plan from typed held-out facts."""
    transformation = signature.get("transformation_family")
    profile = signature.get("compatibility_profile")
    family = signature.get("mechanism_family")
    if not transformation or not profile or not family:
        raise ValueError("held-out mechanism signature lacks family/profile/transformation")
    if arm == "R0":
        return {"mechanism_signature": {"transformation_family": transformation}}
    if arm == "R1":
        return {
            "compatibility_profile": profile,
            "mechanism_signature": {"transformation_family": transformation},
        }
    if arm == "R2":
        return {
            "compatibility_profile": profile,
            "mechanism_signature": {
                "mechanism_family": family,
                "transformation_family": transformation,
            },
            "required_effect": effect_key,
        }
    raise ValueError(f"unknown retrieval arm: {arm}")


def _negative_plan(plan: dict) -> dict:
    """Add a concrete structural mismatch for the controlled negative slice."""
    result = json.loads(json.dumps(plan))
    signature = result.setdefault("mechanism_signature", {})
    signature["module"] = "__unseen_negative_module__"
    return result


def _matches(conn, plan: dict) -> list[dict]:
    return [match.to_dict() for match in retrieve_causal_paths(
        conn, MemoryQuery(query_plan=plan), campaign_id=CAMPAIGN_ID,
        limit=10, include_shadow=True)]


def _hit(matches: list[dict], family: str, *, limit: int = 3) -> bool:
    return any(item.get("mechanism_family") == family for item in matches[:limit])


def _build_report(
    source_db: Path,
    output_dir: Path,
    *,
    training_names: list[str],
    heldout_names: list[str],
    overwrite: bool = False,
) -> dict:
    source_db = source_db.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"output directory is non-empty; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    derived_db = output_dir / "tehm.sqlite"
    _backup_database(source_db, derived_db)
    source_digest_before = _sha256(source_db)
    train_projects = _resolve_projects(training_names, "training")
    heldout_projects = _resolve_projects(heldout_names, "held-out")
    train_lineages = {_lineage(path) for path in train_projects}
    heldout_lineages = {_lineage(path) for path in heldout_projects}
    if train_lineages & heldout_lineages:
        raise ValueError("held-out lineage leaked into training")

    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    store = ArtifactStore(output_dir / "artifacts")
    original_now_local = db.now_local
    db.now_local = lambda: MATERIALIZED_AT
    try:
        # Build one shadow causal fragment per frozen learner transition, then
        # consolidate one path for each mechanism/profile group.  The copied
        # source DB is the only canonical input; all writes are derived shadow
        # objects in ``output_dir``.
        fragments = []
        training_rows = []
        for project in train_projects:
            transition_id = _source_transition(conn, project)
            fragment = build_transition_causal_fragment(
                conn, transition_id, campaign_id=CAMPAIGN_ID)
            if not fragment.learner_eligible:
                raise ValueError(f"training fragment is not learner eligible: {project}")
            fragments.append(fragment)
            training_rows.append({
                "project": project.name,
                "lineage_id": _lineage(project),
                "transition_id": transition_id,
                "mechanism_family": fragment.mechanism_family,
                "compatibility_profile": fragment.compatibility_profile,
            })

        grouped = defaultdict(list)
        for fragment in fragments:
            grouped[(fragment.mechanism_family,
                     fragment.compatibility_profile)].append(fragment)
        paths = []
        for key in sorted(grouped):
            paths.append(consolidate_causal_path(
                conn, grouped[key], campaign_id=CAMPAIGN_ID, status="shadow"))

        cases = []
        heldout_transition_ids = []
        for project in heldout_projects:
            manifest = _manifest(project)
            record = build_rtl_execution_record(project, oracle=IcarusOracle(), store=store)
            if record.verification.get("verdict") != "PASS":
                raise ValueError(f"held-out RTL oracle did not PASS: {project}")
            receipt = capture(
                conn, store, record, dataset_campaign_id=CAMPAIGN_ID,
                dataset_split="heldout", dataset_learner_eligible=False,
                materialized_at=MATERIALIZED_AT)
            heldout_transition_ids.append(receipt.transition_id)
            fragment = build_transition_causal_fragment(
                conn, receipt.transition_id, campaign_id=CAMPAIGN_ID)
            facts = load_transition_facts(conn, receipt.transition_id)
            signature = mechanism_signature(facts)
            effect_key = receipt.primary_effect_key
            arms = {}
            for arm in ("R0", "R1", "R2"):
                plan = _query_plan(signature=signature, effect_key=effect_key, arm=arm)
                matches = _matches(conn, plan)
                # Keep the metadata arms unchanged on the negative slice: a
                # metadata-only selector has no concrete structural witness
                # with which to reject the near miss.  R2 receives the same
                # query plus an unseen module, so its abstention is directly
                # attributable to causal-detail matching rather than a
                # different family/profile filter.
                negative_plan = _negative_plan(plan) if arm == "R2" else plan
                negative = _matches(conn, negative_plan)
                arms[arm] = {
                    "query_plan": plan,
                    "matches": matches,
                    "positive_hit_at_3": _hit(
                        matches, str(manifest.get("mechanism_family")), limit=3),
                    "negative_query_plan": negative_plan,
                    "negative_matches": negative,
                    "negative_false_transfer": bool(negative),
                }
            cases.append({
                "project": project.name,
                "lineage_id": _lineage(project),
                "transition_id": receipt.transition_id,
                "mechanism_family": signature["mechanism_family"],
                "transformation_family": signature["transformation_family"],
                "compatibility_profile": signature["compatibility_profile"],
                "effect_key": effect_key,
                "oracle_verdict": record.verification.get("verdict"),
                "fragment_learner_eligible": fragment.learner_eligible,
                "arms": arms,
            })
        conn.commit()
    finally:
        db.now_local = original_now_local
        conn.close()

    source_digest_after = _sha256(source_db)
    if source_digest_before != source_digest_after:
        raise RuntimeError("source database changed during evaluation")
    path_dicts = [path.to_dict() for path in paths]
    metrics = {}
    for arm in ("R0", "R1", "R2"):
        positives = [case["arms"][arm]["positive_hit_at_3"] for case in cases]
        negatives = [case["arms"][arm]["negative_false_transfer"] for case in cases]
        metrics[arm] = {
            "positive_recall_at_3": round(sum(positives) / len(positives), 6)
            if positives else 0.0,
            "negative_false_transfer_rate": round(sum(negatives) / len(negatives), 6)
            if negatives else 0.0,
            "mean_candidate_count": round(sum(
                len(case["arms"][arm]["matches"]) for case in cases
            ) / len(cases), 6) if cases else 0.0,
        }
    report = {
        "version": REPORT_VERSION,
        "evaluation_only": True,
        "campaign_id": CAMPAIGN_ID,
        "source_db": str(source_db),
        "source_db_sha256": source_digest_before,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "training_projects": [path.name for path in train_projects],
        "heldout_projects": [path.name for path in heldout_projects],
        "training": {
            "rows": training_rows,
            "path_count": len(path_dicts),
            "paths": path_dicts,
        },
        "cases": cases,
        "metrics": metrics,
        "firewall": {
            "training_source_only": all(
                set(path.get("source_transition_ids", [])) <= {
                    row["transition_id"] for row in training_rows
                } for path in path_dicts),
            "heldout_in_training_paths": any(
                transition_id in {
                    source_id for path in path_dicts
                    for source_id in path.get("source_transition_ids", [])
                } for transition_id in heldout_transition_ids),
            "heldout_learner_eligible": False,
            "canonical_memory_mutation": "none",
        },
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "report_digest": None,
    }
    report["firewall"]["heldout_in_training_paths"] = bool(
        report["firewall"]["heldout_in_training_paths"])
    digest_payload = dict(report)
    digest_payload["report_digest"] = None
    report["report_digest"] = _stable_digest(digest_payload)
    report_path = output_dir / "causal_retrieval_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-db", type=Path,
        default=ROOT.parent / "evidence" / "tehm-evidence-freeze-v4-dev" /
        "closed_loop" / "tehm.sqlite")
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT.parent / "evidence" / "tehm-causal-retrieval-rtl-r1-dev")
    parser.add_argument("--training", action="append", dest="training",
                        help="fixture directory name (repeatable)")
    parser.add_argument("--heldout", action="append", dest="heldout",
                        help="held-out fixture directory name (repeatable)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    report = _build_report(
        args.source_db, args.output_dir,
        training_names=args.training or list(DEFAULT_TRAINING),
        heldout_names=args.heldout or list(DEFAULT_HELDOUT),
        overwrite=args.overwrite)
    print(json.dumps({
        "version": report["version"],
        "source_db_sha256": report["source_db_sha256"],
        "derived_db_sha256": report["derived_db_sha256"],
        "path_count": report["training"]["path_count"],
        "metrics": report["metrics"],
        "canonical_memory_mutation": report["firewall"]["canonical_memory_mutation"],
        "promotion_attempted": report["promotion_attempted"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
