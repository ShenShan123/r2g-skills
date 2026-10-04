"""Offline scope sensitivity audit. Never imports the runner or starts EDA."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent / "full_r2g_supplement_208"
STRATEGY = "hierarchical_place_timing_repair"


def keep(selection, minimum_wns):
    """Use pre-action evidence only; retain uncertain inputs for manual review."""
    if selection.get("candidate_id") != STRATEGY:
        return True
    evidence = selection.get("native_selection", {}).get("setup_scope_evidence", {})
    wns = evidence.get("wns_ns")
    if (isinstance(wns, bool) or not isinstance(wns, (int, float))
            or not math.isfinite(wns)):
        return True
    return wns >= minimum_wns


def effect_key(selection):
    return json.dumps({key: selection.get(key, {}) for key in
                       ("config_edits", "sdc_edits", "env", "env_flags")}, sort_keys=True)


def evaluate(rows, minimum_wns):
    output = []
    for row in rows:
        retained = [a for a in row["attempts"] if keep(a["selection"], minimum_wns)]
        skipped = [a for a in row["attempts"] if not keep(a["selection"], minimum_wns)]
        output.append({
            "task_id": row["task_id"], "original_clean": row["original_clean"],
            "retained_calls": len(retained), "skipped_calls": len(skipped),
            "replayed_clean": any(a["clean"] for a in retained),
            "skipped_historical_attempt_seconds": sum(a["seconds"] for a in skipped),
        })
    return {
        "minimum_wns_ns": minimum_wns,
        "retained_calls": sum(r["retained_calls"] for r in output),
        "skipped_calls": sum(r["skipped_calls"] for r in output),
        "replayed_clean": sum(r["replayed_clean"] for r in output),
        "lost_successes": [r["task_id"] for r in output
                           if r["original_clean"] and not r["replayed_clean"]],
        "skipped_historical_attempt_seconds": sum(
            r["skipped_historical_attempt_seconds"] for r in output),
        "tasks": output,
    }


def main():
    hashes = {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    result = read(RUN / "complete.json")
    rows, duplicate_effects = [], 0
    for record in result["results"]:
        seen, attempts = set(), []
        for number, evidence in enumerate(record["attempts"], 1):
            selection = read(RUN / "tasks" / record["task_id"] / f"selection_{number:02d}.json")
            assert selection["candidate_id"] == evidence["candidate_id"]
            key = effect_key(selection)
            duplicate_effects += key in seen
            seen.add(key)
            if selection["candidate_id"] == STRATEGY:
                assert (selection["native_selection"]["setup_scope_evidence"]["wns_ns"]
                        == evidence["baseline_metrics"]["setup_wns_ns"])
            attempts.append({"selection": selection,
                             "clean": evidence["strict_clean_after_repair"],
                             "seconds": evidence["elapsed_seconds"]})
        assert len(attempts) == record["attempt_count"]
        rows.append({"task_id": record["task_id"],
                     "original_clean": record["strict_clean"], "attempts": attempts})
    assert len(rows) == 13 and result["strict_clean"] == 8
    scenarios = [evaluate(rows, bound) for bound in (-3.0, -2.5, -2.0, -1.0)]
    assert scenarios[0]["replayed_clean"] == result["strict_clean"]
    assert scenarios[0]["retained_calls"] == sum(len(r["attempts"]) for r in rows)
    # Check that the archived inputs did not change during the audit.
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    report = {
        "mode": "post_hoc_offline_sensitivity_not_new_experimental_score",
        "original_calls": scenarios[0]["retained_calls"],
        "original_clean": result["strict_clean"], "denominator": len(rows),
        "duplicate_effect_calls_within_task": duplicate_effects,
        "scenarios": scenarios, "input_sha256": hashes,
        "oracle_only_upper_bound_skippable_calls": sum(
            len(r["attempts"]) for r in rows if not r["original_clean"]),
        "recommendation": "keep_production_scope_unchanged",
        "limitations": [
            "Historical outcomes are used only for scoring the replay, not the keep predicate.",
            "Thresholds are post-hoc sensitivity checks, not a validated new selection policy.",
            "Skipped attempt durations are not measured end-to-end runtime savings.",
            "No alternate strategy execution or new physical signoff is simulated.",
            "No evidence supports a path-structure discriminator that retains all successes yet rejects failures.",
            "Cross-run failure-cache savings cannot be claimed for a first encounter.",
        ],
        "production_modified": False, "eda_launched": False,
    }
    (ROOT / "replay_results.json").write_text(json.dumps(report, indent=2) + "\n")
    for s in scenarios:
        print(f"WNS >= {s['minimum_wns_ns']}: {s['retained_calls']} calls, "
              f"{s['replayed_clean']}/13 clean, lost={s['lost_successes']}")
    print(f"Duplicate effects within a task: {duplicate_effects}")


if __name__ == "__main__":
    main()
