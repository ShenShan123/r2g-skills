#!/usr/bin/env python3
"""Continuously expand in auditable micro-cohorts until a DesignFamily target."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from corpus_state import CorpusState, utc_now
from process_runner import run_streamed
from split_consumption_contract import FINAL_FROZEN, INTERNAL, load_and_validate, transition_state


SCHEMA = "rtl_design_family_target_controller_v1"
RECOVERABLE_CHILD_STATES = {
    "INTERRUPTED_RECOVERABLE",
    "ACQUIRING",
    "ACQUIRING_PIPELINED",
    "ACQUIRING_BACKOFF",
    "TARGET_REACHED_PENDING_LOCK",
    "COHORT_LOCKED_DRAINING",
    "RECONCILING",
    "AUTO_RECONCILING",
    "FINALIZING",
}


def child_controller_state(corpus: Path, round_id: str) -> str:
    path = corpus / "quality/phase2/rounds" / round_id / "target_controller.json"
    if not path.is_file():
        return "MISSING"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("state") or "UNKNOWN")
    except (OSError, json.JSONDecodeError):
        return "UNREADABLE"


def child_failure_is_automatically_recoverable(corpus: Path, round_id: str) -> bool:
    """Resume only unambiguous interrupted/nonterminal states; never correctness failures."""
    return child_controller_state(corpus, round_id) in RECOVERABLE_CHILD_STATES


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def metrics(corpus: Path) -> dict[str, Any]:
    with CorpusState(corpus) as state:
        if not state.populated():
            state.sync_materialized_views()
        return {"schema": SCHEMA, **state.metrics()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--local-source-root", type=Path, default=Path.home() / "work/_downloads")
    parser.add_argument("--target-global-design-families", type=int, required=True)
    parser.add_argument("--revision-batch", type=int, default=2_000)
    parser.add_argument("--min-revision-batch", type=int, default=500)
    parser.add_argument("--max-revision-batch", type=int, default=3_000)
    parser.add_argument("--assumed-family-yield", type=float, default=0.40)
    parser.add_argument("--max-seconds-per-new-family", type=float, default=120.0)
    parser.add_argument("--large-xlarge-soft-target", type=float, default=0.08)
    parser.add_argument("--large-xlarge-stretch-target", type=float, default=0.10)
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--providers", default="github,gitlab,codeberg,fusesoc")
    parser.add_argument("--process-budget", type=int, default=600)
    parser.add_argument("--max-repo-seconds", type=int, default=900)
    parser.add_argument(
        "--pipelined-processing", action=argparse.BooleanOptionalAction, default=True,
        help="Process revision-local artifacts concurrently with acquisition",
    )
    parser.add_argument("--pipeline-workers", type=int, default=2)
    parser.add_argument("--pipeline-poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--child-resume-backoff-seconds", type=float, default=30.0,
        help="Backoff before automatically resuming an unambiguously recoverable child round",
    )
    args = parser.parse_args()
    if args.target_global_design_families <= 0 or args.revision_batch <= 0:
        raise SystemExit("family target and revision batch must be positive")
    objective_dir = args.corpus_root / "state/controllers" / args.objective_id
    state_path = objective_dir / "controller.json"
    lock_path = objective_dir / ".controller.lock"
    objective_dir.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"schema": SCHEMA, "state": "ALREADY_RUNNING"}, indent=2))
            return 2
        current = metrics(args.corpus_root)
        state = json.loads(state_path.read_text()) if state_path.is_file() else {
            "schema": SCHEMA, "objective_id": args.objective_id,
            "primary_metric": "global_unique_provenance_complete_synthesis_valid_design_families",
            "target": args.target_global_design_families,
            "created_at": utc_now(), "batches": [],
            "repository_revision_target": {"mode": "ADAPTIVE", "hard_completion_gate": False},
            "hard_completion_target": {
                "metric": "global_unique_provenance_complete_synthesis_valid_design_families",
                "functional_confidence_minimum": None,
                "license_status_minimum": None,
            },
            "nonblocking_lanes": {
                "license_recovery": {
                    "blocking": False,
                    "unknown_allowed_in_raw_and_formal_internal": True,
                    "unknown_allowed_in_public_export": False,
                },
                "functional_evidence_promotion": {
                    "blocking": False, "base_family_gate": "F0_OR_HIGHER",
                    "derived_subset": "FUNCTIONAL_VERIFIED_F2_PLUS",
                },
                "large_xlarge_discovery": {
                    "blocking": False, "target": args.large_xlarge_soft_target,
                    "stretch_target": args.large_xlarge_stretch_target,
                    "signal_policy": "UPSTREAM_SCALE_EVIDENCE_PLUS_DIRECT_RTL_ANCHOR",
                },
            },
            "quality_and_completion_gates": "UNCHANGED",
        }
        if int(state.get("target", -1)) != args.target_global_design_families:
            raise ValueError("saved DesignFamily target differs from requested target")
        scripts = Path(__file__).parent
        while int(current["formal_synthesis_valid_families"]) < args.target_global_design_families:
            before = int(current["formal_synthesis_valid_families"])
            remaining = args.target_global_design_families - before
            observed = [
                float(row["family_yield"]) for row in state["batches"]
                if float(row.get("family_yield", 0)) > 0
            ]
            expected_yield = observed[-1] if observed else args.assumed_family_yield
            needed = math.ceil(remaining / max(0.05, expected_yield))
            revision_target = min(args.max_revision_batch, max(args.min_revision_batch, min(args.revision_batch, needed)))
            if state["batches"] and float(state["batches"][-1].get("seconds_per_new_family", 0)) > args.max_seconds_per_new_family:
                revision_target = max(args.min_revision_batch, revision_target // 2)
            unfinished = (
                state["batches"][-1]
                if state["batches"] and state["batches"][-1].get("state") in {"RUNNING", "FAIL"}
                else None
            )
            sequence = int(unfinished["sequence"]) if unfinished else len(state["batches"]) + 1
            round_id = str(unfinished["round_id"]) if unfinished else (
                "p2f_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
                + f"_{args.objective_id}_batch{sequence:04d}"
            )
            revision_target = int(unfinished["revision_target"]) if unfinished else revision_target
            command = [
                sys.executable, str(scripts / "run_until_revision_target.py"),
                "--corpus-root", str(args.corpus_root), "--local-source-root", str(args.local_source_root),
                "--factory-round-id", round_id, "--target-new-acquired", str(revision_target),
                "--providers", args.providers, "--process-budget", str(args.process_budget),
                "--max-repo-seconds", str(args.max_repo_seconds),
                "--marginal-large-xlarge-target", str(args.large_xlarge_soft_target),
                "--marginal-large-xlarge-stretch", str(args.large_xlarge_stretch_target),
                "--pipeline-workers", str(args.pipeline_workers),
                "--pipeline-poll-seconds", str(args.pipeline_poll_seconds),
            ]
            if not args.pipelined_processing:
                command.append("--no-pipelined-processing")
            batch = unfinished or {
                "sequence": sequence, "round_id": round_id, "state": "RUNNING",
                "revision_target": revision_target, "families_before": before, "started_at": utc_now(),
            }
            if unfinished is None:
                state["batches"].append(batch)
            else:
                before = int(batch["families_before"])
                batch["state"] = "RUNNING"
                batch["resumed_at"] = utc_now()
            state.update({"state": "EXPANDING", "current": before, "remaining": remaining,
                          "active_round_id": round_id, "updated_at": utc_now()})
            atomic(state_path, state)

            def heartbeat(progress: dict[str, Any]) -> None:
                state.update({"heartbeat_at": progress["heartbeat_at"], "active_child_pid": progress["child_pid"],
                              "active_child_last_output_at": progress["last_output_at"], "updated_at": progress["heartbeat_at"]})
                atomic(state_path, state)

            result = run_streamed(command, objective_dir / "logs", round_id, heartbeat=heartbeat)
            batch.update({"state": "PASS" if result["returncode"] == 0 else "FAIL",
                          "returncode": result["returncode"], "completed_at": utc_now(),
                          "stdout_log": result["stdout_log"], "stderr_log": result["stderr_log"]})
            if result["returncode"] != 0:
                child_state = child_controller_state(args.corpus_root, round_id)
                if child_failure_is_automatically_recoverable(args.corpus_root, round_id):
                    failures = batch.setdefault("recoverable_exit_history", [])
                    failures.append({
                        "returncode": result["returncode"],
                        "child_controller_state": child_state,
                        "observed_at": utc_now(),
                        "stderr_tail": result.get("stderr_tail", ""),
                    })
                    batch["state"] = "RUNNING"
                    state.update({
                        "state": "RECOVERING_CHILD_ROUND",
                        "recovering_round_id": round_id,
                        "recoverable_child_exit_count": len(failures),
                        "next_resume_after_seconds": max(0.0, args.child_resume_backoff_seconds),
                        "updated_at": utc_now(),
                    })
                    atomic(state_path, state)
                    time.sleep(max(0.0, args.child_resume_backoff_seconds))
                    continue
                state.update({"state": "FAILED_CHILD_ROUND", "updated_at": utc_now()})
                state["failed_child_controller_state"] = child_state
                atomic(state_path, state)
                print(json.dumps(state, indent=2, sort_keys=True))
                return 1
            current = metrics(args.corpus_root)
            after = int(current["formal_synthesis_valid_families"])
            cohort_path = args.corpus_root / "quality/phase2/rounds" / round_id / "cohort_lock.json"
            cohort = json.loads(cohort_path.read_text()) if cohort_path.is_file() else {}
            acquired = int(
                cohort.get("actual_cohort_size", cohort.get(
                    "acquired_revision_count", cohort.get("cohort_size", revision_target)
                ))
            )
            batch.update({"families_after": after, "new_families": after - before,
                          "acquired_revisions": acquired,
                          "requested_revisions": revision_target,
                          "early_close": bool(cohort.get("early_close", False)),
                          "early_close_reason": cohort.get("early_close_reason"),
                          "elapsed_seconds": result["elapsed_seconds"],
                          "seconds_per_new_family": round(result["elapsed_seconds"] / max(1, after - before), 6),
                          "family_yield": round((after - before) / max(1, acquired), 6)})
            state["updated_at"] = utc_now()
            atomic(state_path, state)
        snapshot_id = state.get("certification_snapshot_id") or (
            f"family-target-{args.objective_id}-{args.target_global_design_families}"
        )
        state.update({"state": "CERTIFYING", "certification_snapshot_id": snapshot_id,
                      "updated_at": utc_now()})
        atomic(state_path, state)
        consumption_state_path = objective_dir / "split_profile_consumption_state.json"
        if consumption_state_path.is_file():
            _, _, consumption_state = load_and_validate(
                args.corpus_root, args.objective_id, allowed_states={INTERNAL, FINAL_FROZEN},
            )
            if consumption_state["consumption_state"] == INTERNAL:
                candidate_snapshot_id = state.get("internal_candidate_snapshot_id") or (
                    f"{snapshot_id}-internal-candidate"
                )
                state.update({
                    "state": "CERTIFYING_CAMPAIGN_INTERNAL_CANDIDATE",
                    "internal_candidate_snapshot_id": candidate_snapshot_id,
                    "updated_at": utc_now(),
                })
                atomic(state_path, state)
                candidate = run_streamed(
                    [sys.executable, str(scripts / "build_corpus_snapshot.py"),
                     "--corpus-root", str(args.corpus_root), "--snapshot-id", candidate_snapshot_id],
                    objective_dir / "logs", "certification-internal-candidate",
                )
                candidate_root = args.corpus_root / "snapshots" / candidate_snapshot_id
                candidate_completion = json.loads(
                    (candidate_root / "completion.json").read_text(encoding="utf-8")
                ) if (candidate_root / "completion.json").is_file() else {}
                if (
                    candidate["returncode"] != 0
                    or candidate_completion.get("status") != "CERTIFIED"
                    or candidate_completion.get("consumption_state") != INTERNAL
                    or candidate_completion.get("external_training_eligible") is not False
                    or candidate_completion.get("external_evaluation_eligible") is not False
                ):
                    state.update({
                        "state": "FAILED_INTERNAL_CANDIDATE_CERTIFICATION",
                        "updated_at": utc_now(),
                        "certification_stderr_tail": candidate["stderr_tail"],
                    })
                    atomic(state_path, state)
                    print(json.dumps(state, indent=2, sort_keys=True))
                    return 1
                candidate_identity = json.loads(
                    (candidate_root / "release_identity.json").read_text(encoding="utf-8")
                )
                transition_state(
                    args.corpus_root, args.objective_id, FINAL_FROZEN,
                    {
                        "formal_design_families": int(current["formal_synthesis_valid_families"]),
                        "target": args.target_global_design_families,
                        "all_child_rounds_final": all(
                            row.get("state") == "PASS" for row in state["batches"]
                        ),
                        "certified_internal_candidate_snapshot_id": candidate_snapshot_id,
                        "certified_internal_candidate_release_sha256": candidate_identity["release_sha256"],
                    },
                )
                state.update({"state": "CERTIFYING_FINAL_FROZEN", "updated_at": utc_now()})
                atomic(state_path, state)
        certification = run_streamed(
            [sys.executable, str(scripts / "build_corpus_snapshot.py"),
             "--corpus-root", str(args.corpus_root), "--snapshot-id", snapshot_id],
            objective_dir / "logs", "certification",
        )
        if certification["returncode"] != 0:
            state.update({"state": "FAILED_CERTIFICATION", "updated_at": utc_now(),
                          "certification_stderr_tail": certification["stderr_tail"]})
            atomic(state_path, state)
            print(json.dumps(state, indent=2, sort_keys=True))
            return 1
        final_completion_path = args.corpus_root / "snapshots" / snapshot_id / "completion.json"
        final_completion = json.loads(final_completion_path.read_text(encoding="utf-8"))
        if (
            final_completion.get("status") != "CERTIFIED"
            or (
                consumption_state_path.is_file()
                and (
                    final_completion.get("consumption_state") != FINAL_FROZEN
                    or final_completion.get("external_training_eligible") is not True
                    or final_completion.get("external_evaluation_eligible") is not True
                )
            )
        ):
            state.update({"state": "FAILED_FINAL_FROZEN_CERTIFICATION", "updated_at": utc_now()})
            atomic(state_path, state)
            print(json.dumps(state, indent=2, sort_keys=True))
            return 1
        state.update({
            "state": "COMPLETE", "current": int(current["formal_synthesis_valid_families"]),
            "remaining": 0, "completed_at": utc_now(), "updated_at": utc_now(),
            "completion": {
                "primary_target_met": True,
                "all_child_rounds_final": all(row.get("state") == "PASS" for row in state["batches"]),
                "metric_schema": "rtl_family_v1",
                "certified_snapshot_id": snapshot_id,
            },
        })
        state.pop("active_child_pid", None)
        state.pop("active_round_id", None)
        atomic(state_path, state)
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
