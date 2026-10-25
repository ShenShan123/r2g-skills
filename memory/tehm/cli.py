#!/usr/bin/env python3
"""TEHM subcommand CLI.

Usage (run from the memory/ workdir, or anywhere with ``memory/`` on PYTHONPATH):
    python3 tehm/cli.py init-db            [--db PATH] [--artifacts PATH]
    python3 tehm/cli.py capture RECORD.json [--db PATH] [--artifacts PATH]
    python3 tehm/cli.py capture-r2g PROJECT [--db PATH] [--artifacts PATH]
    python3 tehm/cli.py preflight [--out-dir DIR] [--db PATH]
    python3 tehm/cli.py crystallize [--min-group-size N] [--dry-run] [--db PATH]
    python3 tehm/cli.py retrieve --check CHECK [--project DIR] [--limit N] [--db PATH]
    python3 tehm/cli.py health              [--db PATH] [--artifacts PATH]
    python3 tehm/cli.py honesty             [--db PATH] [--artifacts PATH]

Backend lock is per-process (design doc 17.3): ``TEHM_DB`` / ``--db`` select the
TEHM store only; legacy memory is never opened (H5).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Self-bootstrap: when run as ``python3 tehm/cli.py``, the memory/ package root
# (parent of this file's dir) must be importable. Mirrors the repo's knowledge/
# sys.path bootstrap convention.
_MEMORY_ROOT = Path(__file__).resolve().parent.parent
if str(_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_MEMORY_ROOT))

from tehm import config, db
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import ExecutionRecord, capture
from tehm import honesty as tehm_honesty


def _open(args) -> tuple:
    db_path = config.db_path_from_env_or(args.db)
    conn = db.connect(db_path)
    db.ensure_schema(conn)
    artifact_root = Path(args.artifacts) if args.artifacts else config.default_artifact_root()
    store = ArtifactStore(artifact_root)
    return conn, store, db_path


def cmd_init_db(args: argparse.Namespace) -> int:
    conn, _, db_path = _open(args)
    n_tables = len(conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    print(f"tehm store initialised at {db_path}")
    print(f"  schema_version : {db._meta_get(conn, 'schema_version', None)}")
    print(f"  tables         : {n_tables}")
    conn.close()
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    conn, store, _ = _open(args)
    data = json.loads(Path(args.record).read_text())
    receipt = capture(conn, store, ExecutionRecord.from_dict(data))
    print(json.dumps(receipt.to_dict(), indent=2, sort_keys=True))
    conn.close()
    return 0


def cmd_capture_r2g(args: argparse.Namespace) -> int:
    from tehm.adapters.r2g_evidence import capture_r2g_project

    conn, store, _ = _open(args)
    receipts = capture_r2g_project(conn, store, Path(args.project))
    print(json.dumps([r.to_dict() for r in receipts], indent=2, sort_keys=True))
    print(f"captured {len(receipts)} transition(s) from {args.project}",
          file=sys.stderr)
    conn.close()
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    from contracts import RepairContext
    from tehm.retrieval.pipeline import retrieve as do_retrieve

    conn, _, _ = _open(args)
    # Optional project dir supplies a richer repair context (reports + config).
    context = RepairContext(check=args.check, design_id=args.design,
                            platform=args.platform)
    if args.project:
        from tehm.adapters.r2g_evidence import collect_execution_evidence

        ev = collect_execution_evidence(Path(args.project))
        context = RepairContext(
            design_id=ev["config"].get("DESIGN_NAME"),
            platform=ev["config"].get("PLATFORM"),
            check=args.check,
            reports=ev["reports"],
            cfg=ev["config"],
        )
    receipt = do_retrieve(conn, context, limit=args.limit)
    print(f"recalled {receipt.candidates_retrieved}, "
          f"applicable={receipt.applicable}, inapplicable={receipt.inapplicable}, "
          f"unresolved={receipt.unresolved}  ({receipt.latency_ms} ms)")
    if not receipt.results:
        print("no admissible rule retrieved (cold start / no match)")
    for r in receipt.results:
        print(f"  {r.score:.4f}  [{r.applicability_status}]  "
              f"{r.transformation_family}  {r.rule_id}  "
              f"(sim={r.similarity:.2f}, risk_pen={r.risk_penalty:.2f})")
    conn.close()
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    from contracts import RepairContext
    from tehm.activation.pipeline import activate as do_activate

    conn, store, _ = _open(args)
    context = RepairContext(check=args.check, design_id=args.design,
                            platform=args.platform)
    binding = json.loads(args.binding) if args.binding else None
    record = do_activate(conn, store, rule_id=args.rule, context=context,
                         provided_binding=binding, dry_run=args.dry_run,
                         authority_mode=args.authority_mode)
    print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    print(f"activation axes: applicable={record.applicability_status}, "
          f"executable={record.executability_status}, "
          f"verifiable={record.verification_status}  "
          f"(outcome={record.outcome})", file=sys.stderr)
    conn.close()
    return 0


def cmd_physical_profile(args: argparse.Namespace) -> int:
    from tehm.physical.memory import PhysicalEffectMemory

    conn, _, _ = _open(args)
    profile = PhysicalEffectMemory(conn).profile(family=args.family,
                                                 effect_key=args.effect_key)
    print(json.dumps(profile, indent=2, sort_keys=True))
    conn.close()
    return 0


def cmd_physical_record(args: argparse.Namespace) -> int:
    from tehm.physical.memory import PhysicalEffectMemory

    conn, _, _ = _open(args)
    before = json.loads(Path(args.before).read_text())
    after = json.loads(Path(args.after).read_text())
    effect = PhysicalEffectMemory(conn).record(
        transition_id=args.transition, action_domain=args.action_domain,
        transformation_family=args.family, before_ppa=before, after_ppa=after)
    print(json.dumps(effect.to_row(), indent=2, sort_keys=True))
    conn.close()
    return 0


def cmd_physical_predict(args: argparse.Namespace) -> int:
    """Retrieve compatible similar graph contexts with uncertainty gates."""
    from tehm.physical.memory import PhysicalEffectMemory

    conn, _, _ = _open(args)
    context = json.loads(Path(args.graph_context).read_text())
    policy = (json.loads(Path(args.calibration_policy).read_text())
              if args.calibration_policy else None)
    result = PhysicalEffectMemory(conn).predict(
        family=args.family, effect_key=args.effect_key, graph_context=context,
        k=args.k, min_unique_contexts=args.min_unique_contexts,
        max_distance=args.max_distance, calibration_policy=policy)
    print(json.dumps(result, indent=2, sort_keys=True))
    conn.close()
    return 2 if result.get("abstained") else 0


def cmd_physical_calibrate(args: argparse.Namespace) -> int:
    """Calibrate retrieval without ingesting held-out observations."""
    from tehm.physical.calibration import calibrate_retrieval
    from tehm.physical.memory import PhysicalEffectMemory

    payload = json.loads(Path(args.samples).read_text())
    samples = payload.get("samples", []) if isinstance(payload, dict) else payload
    training = payload.get("training_lineages", []) if isinstance(payload, dict) else []
    conn, _, _ = _open(args)
    policy = calibrate_retrieval(
        PhysicalEffectMemory(conn), family=args.family,
        heldout_samples=samples, training_lineages=training, k=args.k,
        min_unique_contexts=args.min_unique_contexts,
        min_samples=args.min_samples, target_coverage=args.target_coverage,
        distance_ceiling=args.distance_ceiling,
        distance_quantile=args.distance_quantile,
        uncertainty_quantile=args.uncertainty_quantile)
    print(json.dumps(policy, indent=2, sort_keys=True))
    conn.close()
    return 0 if policy.get("status") == "ready" else 2


def cmd_crystallize(args: argparse.Namespace) -> int:
    from tehm.crystallization.build_rules import crystallize_all

    conn, _, _ = _open(args)
    rules = crystallize_all(conn, min_group_size=args.min_group_size,
                            dry_run=args.dry_run, campaign_id=args.campaign_id)
    print(f"crystallized {len(rules)} audited rule(s)")
    for rule in rules:
        print(f"  {rule['rule_id']}  {rule['transformation_family']}  "
              f"[{rule['validity_status']}]  episodes={rule['provenance']['source_episodes']}")
        print(f"    before: {rule['before_pattern']}")
        print(f"    after : {rule['after_pattern']}")
        gates = rule["validity_profile"].get("gates", [])
        print("    gates : " + ", ".join(
            f"{g['name']}={g['ok']}" for g in gates))
        risks = rule.get("risk_profile") or []
        if risks:
            print("    risk  : " + ", ".join(
                f"{r['risk']}x{r['support']}" for r in risks))
    if args.dry_run:
        print("(dry-run: nothing written)")
    conn.close()
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    from tehm.crystallization.preflight import run_preflight

    conn, _, _ = _open(args)
    report = run_preflight(conn, out_dir=args.out_dir,
                           min_group_size=args.min_group_size,
                           top_groups=args.top_groups,
                           campaign_id=args.campaign_id)
    print(f"transitions    : {report.total_transitions}")
    print(f"effect groups  : {report.num_groups}")
    print(f"singleton rate : {report.singleton_rate:.3f}")
    print(f"CC_raw         : {report.cc_raw:.3f}")
    print(f"CC_lineage     : {report.cc_lineage:.3f}")
    print(f"key precision  : {report.key_precision:.3f}  (recall: {report.key_recall:.3f})")
    print(f"VERDICT        : {report.verdict.upper()} — {report.detail}")
    if args.out_dir:
        print(f"outputs written to {args.out_dir}", file=sys.stderr)
    conn.close()
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    conn, _, db_path = _open(args)
    n_states = conn.execute("SELECT COUNT(*) AS n FROM tehm_states").fetchone()["n"]
    n_trans = conn.execute("SELECT COUNT(*) AS n FROM tehm_transitions").fetchone()["n"]
    n_eps = conn.execute("SELECT COUNT(*) AS n FROM tehm_episodes").fetchone()["n"]
    n_views = conn.execute("SELECT COUNT(*) AS n FROM tehm_views").fetchone()["n"]
    n_rules = conn.execute("SELECT COUNT(*) AS n FROM tehm_rules").fetchone()["n"]
    n_acts = conn.execute("SELECT COUNT(*) AS n FROM tehm_activations").fetchone()["n"]
    n_trials = conn.execute("SELECT COUNT(*) AS n FROM tehm_trials").fetchone()["n"]

    # Transition completeness (H1 proxy).
    complete = 0
    if n_trans:
        complete = conn.execute(
            """SELECT COUNT(*) AS n FROM tehm_transitions
               WHERE source_state_id IS NOT NULL AND target_state_id IS NOT NULL
                 AND action_json != '' AND verifier_json != ''""").fetchone()["n"]

    # View materialization coverage: distinct canonical owners with >=1 view.
    if n_eps:
        covered = conn.execute(
            """SELECT COUNT(DISTINCT owner_type || ':' || owner_id) AS n FROM tehm_views""").fetchone()["n"]
    else:
        covered = 0
    total_owners = 2 * n_trans + n_trans + n_eps
    coverage = (covered / total_owners) if total_owners else 0.0

    # Singleton rate on the primary effect key (design doc 6.3 proxy).
    singleton_rate = None
    if n_trans:
        groups = conn.execute(
            """SELECT primary_effect_key, COUNT(*) AS n FROM tehm_transitions
               GROUP BY primary_effect_key""").fetchall()
        if groups:
            n_singletons = sum(1 for g in groups if g["n"] == 1)
            singleton_rate = n_singletons / len(groups)

    print(f"tehm store     : {db_path}")
    print(f"  states       : {n_states}")
    print(f"  transitions  : {n_trans}  (complete: {complete}/{n_trans})")
    print(f"  episodes     : {n_eps}")
    print(f"  views        : {n_views}  (owner coverage: {coverage:.0%})")
    print(f"  rules        : {n_rules}")
    print(f"  activations  : {n_acts}")
    print(f"  trials       : {n_trials}")
    if singleton_rate is not None:
        print(f"  effect singleton rate: {singleton_rate:.2f}")
    conn.close()
    return 0


def cmd_honesty(args: argparse.Namespace) -> int:
    conn, store, db_path = _open(args)
    all_ok, report = tehm_honesty.run_all(conn, store, db_path)
    for gate in tehm_honesty.HARD_CHECKS:
        status = "OK  " if report[gate[0]]["ok"] else "FAIL"
        print(f"[{gate[0]}] {status}  {report[gate[0]]['detail']}")
    conn.close()
    if not all_ok:
        print("HONESTY BREACH")
        return 1
    print("ALL GATES GREEN")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="TEHM store path (default $TEHM_DB or memory/tehm.sqlite)")
    ap.add_argument("--artifacts", default=None, help="artifact store root (default memory/artifacts)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create/verify the TEHM store schema")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("capture", help="capture one ExecutionRecord JSON into the canonical store")
    p.add_argument("record", help="path to an ExecutionRecord JSON file")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("capture-r2g", help="capture all repair transitions from a real R2G project dir")
    p.add_argument("project", help="path to a real R2G project dir (reports/*.json + fix_log.jsonl)")
    p.set_defaults(func=cmd_capture_r2g)

    p = sub.add_parser("preflight", help="crystallizability preflight over captured transitions")
    p.add_argument("--out-dir", default=None, help="write the 5 preflight outputs here (default: no files)")
    p.add_argument("--min-group-size", type=int, default=2)
    p.add_argument("--top-groups", type=int, default=10)
    p.add_argument("--campaign-id", default="live",
                   help="dataset membership campaign whose learner-eligible rows are used")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("crystallize", help="Phase 5: anti-unify effect groups and synthesize candidate rules")
    p.add_argument("--min-group-size", type=int, default=2)
    p.add_argument("--campaign-id", default="live",
                   help="dataset membership campaign whose learner-eligible rows are used")
    p.add_argument("--dry-run", action="store_true", help="report the candidate rules without writing tehm_rules")
    p.set_defaults(func=cmd_crystallize)

    p = sub.add_parser("retrieve", help="Phase 7: retrieve admissible rules for the current repair state")
    p.add_argument("--check", required=True, help="failing check (drc | lvs | timing | route)")
    p.add_argument("--project", default=None, help="optional R2G project dir to enrich the repair context")
    p.add_argument("--design", default=None, help="design id (optional)")
    p.add_argument("--platform", default=None, help="platform (optional)")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("physical-profile", help="Phase 11: empirical physical-effect profile of an action")
    p.add_argument("--family", default=None, help="transformation family")
    p.add_argument("--effect-key", default=None)
    p.set_defaults(func=cmd_physical_profile)

    p = sub.add_parser("physical-record", help="Phase 11: record one observed physical effect from before/after PPA")
    p.add_argument("--transition", required=True, help="transition_id")
    p.add_argument("--family", required=True, help="transformation family")
    p.add_argument("--action-domain", default="signoff.REPAIR_ACTION")
    p.add_argument("--before", required=True, help="before ppa.json")
    p.add_argument("--after", required=True, help="after ppa.json")
    p.set_defaults(func=cmd_physical_record)

    p = sub.add_parser(
        "physical-predict",
        help="Phase 11: similar-graph empirical prediction with uncertainty/abstain")
    p.add_argument("--family", required=True, help="transformation family")
    p.add_argument("--effect-key", default=None)
    p.add_argument("--graph-context", required=True,
                   help="PhysicalGraphContext JSON for the query design")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--min-unique-contexts", type=int, default=3)
    p.add_argument("--max-distance", type=float, default=3.0)
    p.add_argument("--calibration-policy", default=None,
                   help="held-out calibration policy JSON; non-ready policies abstain")
    p.set_defaults(func=cmd_physical_predict)

    p = sub.add_parser(
        "physical-calibrate",
        help="Phase 11: fit distance/coverage/uncertainty gates on held-out samples")
    p.add_argument("--family", required=True, help="transformation family")
    p.add_argument("--samples", required=True,
                   help="JSON list, or {samples,training_lineages}; never ingested")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--min-unique-contexts", type=int, default=3)
    p.add_argument("--min-samples", type=int, default=3)
    p.add_argument("--target-coverage", type=float, default=0.80)
    p.add_argument("--distance-ceiling", type=float, default=3.0,
                   help="hard OOD safety ceiling; calibration may tighten, never relax it")
    p.add_argument("--distance-quantile", type=float, default=0.95)
    p.add_argument("--uncertainty-quantile", type=float, default=0.95)
    p.set_defaults(func=cmd_physical_calibrate)

    p = sub.add_parser("activate", help="Phase 8: run the eight-step activation for an admissible rule (inspection)")
    p.add_argument("--rule", required=True, help="admissible rule_id")
    p.add_argument("--check", required=True, help="failing check")
    p.add_argument("--binding", default=None, help="JSON binding {hole: value} for the rule's holes")
    p.add_argument("--design", default=None)
    p.add_argument("--platform", default=None)
    p.add_argument("--dry-run", action="store_true", help="show the plan without persisting")
    p.add_argument("--authority-mode", choices=("production", "evaluation", "audit"),
                   default="production",
                   help="production requires a promoted rule; evaluation/audit are explicit escapes")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("health", help="TEHM store health summary")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("honesty", help="run the TEHM honesty gates")
    p.set_defaults(func=cmd_honesty)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
