"""TEHM honesty gates (design doc 27.3).

The public gate IDs and semantics in this module intentionally match the design
document exactly.  Older helper names are retained as compatibility aliases,
but the unified report is the authoritative H1--H12 registry.  Gates that do
not apply to a store (for example H7 on a snapshot with no activations) return
an explicit ``not_applicable`` detail rather than silently disappearing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from tehm.graph.local_design_graph import load_graph_from_artifact
from tehm.graph.predicates import TRUTH_VALUES, extract_predicates
from tehm import db as tehm_db


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
    return int(row["n"])


def h1_transition_completeness(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Every transition has source state, action, target state, verifier snapshot."""
    total = _count(conn, "tehm_transitions")
    if total == 0:
        return True, "no transitions (vacuously complete)"
    rows = conn.execute(
        """SELECT transition_id, source_state_id, target_state_id,
                  action_json, verifier_json
           FROM tehm_transitions""").fetchall()
    bad = []
    for r in rows:
        if not r["source_state_id"] or not r["target_state_id"]:
            bad.append(f"{r['transition_id']}:missing-state")
        elif not r["action_json"] or not r["verifier_json"]:
            bad.append(f"{r['transition_id']}:missing-action-or-verifier")
    # FK: source/target states must exist in tehm_states.
    missing = conn.execute(
        """SELECT t.transition_id FROM tehm_transitions t
           LEFT JOIN tehm_states s1 ON s1.state_id = t.source_state_id
           LEFT JOIN tehm_states s2 ON s2.state_id = t.target_state_id
           WHERE s1.state_id IS NULL OR s2.state_id IS NULL""").fetchall()
    bad += [f"{r['transition_id']}:dangling-state" for r in missing]
    if bad:
        return False, f"{len(bad)} incomplete transitions: {bad[:5]}"
    return True, f"{total} complete transitions"


def h2_view_provenance(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Every view row traces to a canonical owner + a stamped extractor, and the
    stored payload digest matches a recompute (provenance + integrity)."""
    owner_tables = {
        "state": "tehm_states", "transition": "tehm_transitions",
        "episode": "tehm_episodes", "rule": "tehm_rules",
        "activation": "tehm_activations",
    }
    total = _count(conn, "tehm_views")
    if total == 0:
        return True, "no views (vacuously provenance-clean)"
    bad: list[str] = []
    for view in conn.execute(
            "SELECT owner_type, owner_id, view_type, extractor_version, "
            "payload_json, payload_digest, schema_version FROM tehm_views").fetchall():
        if not view["extractor_version"]:
            bad.append(f"{view['owner_type']}:{view['owner_id']}:no-extractor-version")
        if view["owner_type"] not in owner_tables:
            bad.append(f"{view['owner_type']}:unknown-owner-type")
            continue
        recomputed = None
        try:
            from tehm.views.base import payload_digest
            recomputed = payload_digest(
                view["schema_version"], view["extractor_version"],
                tehm_db.read_json(view["payload_json"]))
        except Exception:  # noqa: BLE001
            recomputed = None
        if recomputed is None or recomputed != view["payload_digest"]:
            bad.append(f"{view['owner_type']}:{view['owner_id']}:digest-mismatch")

    # Owner existence per owner type (dangling view rows are provenance breaks).
    pk_column = {
        "state": "state_id", "transition": "transition_id", "episode": "episode_id",
        "rule": "rule_id", "activation": "activation_id",
    }
    for owner_type, pk in pk_column.items():
        table = owner_tables[owner_type]
        rows = conn.execute(
            f"""SELECT v.owner_id FROM tehm_views v
                LEFT JOIN "{table}" o ON o."{pk}" = v.owner_id
                WHERE v.owner_type = ? AND o."{pk}" IS NULL""",
            (owner_type,)).fetchall()
        for r in rows:
            bad.append(f"{owner_type}:dangling:{r['owner_id']}")

    if bad:
        return False, f"{len(bad)} provenance/integrity issues: {bad[:5]}"
    return True, f"{total} views provenance-clean"


def h3_unknown_not_false(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Re-extract predicates from every stored semantic view and verify the
    extractor contract: values are tri-valued and no ``FALSE`` is produced from
    an insufficient observation (UNKNOWN != FALSE, design doc H3)."""
    views = conn.execute(
        """SELECT owner_id, payload_json FROM tehm_views
           WHERE view_type = 'semantic'""").fetchall()
    if not views:
        return True, "no semantic views (vacuously tri-valued)"
    bad: list[str] = []
    for view in views:
        payload = tehm_db.read_json(view["payload_json"])
        graph = load_graph_from_artifact(payload.get("graph") or {})
        snapshot = extract_predicates(graph)
        for name, obs in snapshot.observations.items():
            if obs.value not in TRUTH_VALUES:
                bad.append(f"{view['owner_id']}:{name}:bad-value:{obs.value}")
            if obs.value == "FALSE" and obs.coverage_scope == "insufficient_observation":
                bad.append(f"{view['owner_id']}:{name}:fabricated-FALSE")
    if bad:
        return False, f"{len(bad)} tri-value violations: {bad[:5]}"
    return True, "all stored semantic views re-extract tri-valid predicates"


def artifact_digest_integrity(conn: sqlite3.Connection, store) -> tuple[bool, str]:
    """Supplemental artifact integrity check (not the design document H4)."""
    states = conn.execute(
        "SELECT state_id, artifact_manifest_json FROM tehm_states").fetchall()
    if not states:
        return True, "no states (no artifacts to verify)"
    bad: list[str] = []
    checked = 0
    for st in states:
        manifest = tehm_db.read_json(st["artifact_manifest_json"])
        for kind, ref in manifest.items():
            if isinstance(ref, dict) and "digest" in ref:
                checked += 1
                if not store.verify(ref):
                    bad.append(f"{st['state_id']}:{kind}:digest-mismatch")
    if bad:
        return False, f"{len(bad)} artifact integrity failures: {bad[:5]}"
    return True, f"{checked} artifacts verified against content-addressed blobs"


def h4_source_witness_integrity(conn: sqlite3.Connection) -> tuple[bool, str]:
    """H4: every rule source has replayable, episode-owned witness substitutions."""
    rows = conn.execute(
        """SELECT rs.rule_id, rs.episode_id, rs.source_substitution_json,
                  e.domain, e.lineage_id
           FROM tehm_rule_sources rs
           LEFT JOIN tehm_episodes e ON e.episode_id = rs.episode_id"""
    ).fetchall()
    if not rows:
        return True, "not_applicable: no rule sources"
    bad: list[str] = []
    for row in rows:
        prefix = f"{row['rule_id']}:{row['episode_id']}"
        if row["domain"] is None:
            bad.append(f"{prefix}:missing-episode")
            continue
        try:
            substitutions = tehm_db.read_json(row["source_substitution_json"])
        except Exception:  # noqa: BLE001
            substitutions = {}
        if not isinstance(substitutions, dict) or not substitutions:
            bad.append(f"{prefix}:empty-source-substitution")
            continue
        owned_by_source = False
        for transition_id in substitutions:
            exists = conn.execute(
                """SELECT 1 FROM tehm_episode_steps
                   WHERE transition_id=? LIMIT 1""", (transition_id,)
            ).fetchone()
            if exists is None:
                bad.append(f"{prefix}:witness-not-owned:{transition_id}")
                continue
            source_owns = conn.execute(
                """SELECT 1 FROM tehm_episode_steps
                   WHERE episode_id=? AND transition_id=? LIMIT 1""",
                (row["episode_id"], transition_id),
            ).fetchone()
            owned_by_source = owned_by_source or source_owns is not None
        if not owned_by_source:
            bad.append(f"{prefix}:source-episode-has-no-witness")
    if bad:
        return False, f"{len(bad)} source witness failures: {bad[:5]}"
    return True, f"{len(rows)} rule sources have episode-owned witnesses"


def h5_validity_order(conn: sqlite3.Connection) -> tuple[bool, str]:
    """H5: V2 must be audited before V1, and both precede admission."""
    rows = conn.execute(
        "SELECT rule_id, validity_status, validity_profile_json FROM tehm_rules"
    ).fetchall()
    if not rows:
        return True, "not_applicable: no rules"
    bad: list[str] = []
    for row in rows:
        profile = tehm_db.read_json(row["validity_profile_json"])
        gates = profile.get("gates") if isinstance(profile, dict) else None
        names = [g.get("name") for g in gates or [] if isinstance(g, dict)]
        if names[:2] != ["V2", "V1"]:
            bad.append(f"{row['rule_id']}:order={names}")
            continue
        if row["validity_status"] in ("PROVISIONAL_VALID", "VALIDATED"):
            by_name = {g.get("name"): g for g in gates if isinstance(g, dict)}
            if not (by_name.get("V2", {}).get("ok") is True and
                    by_name.get("V1", {}).get("ok") is True):
                bad.append(f"{row['rule_id']}:admitted-before-v2-v1")
    if bad:
        return False, f"{len(bad)} validity-order failures: {bad[:5]}"
    return True, f"{len(rows)} rules preserve V2→V1 validity order"


def h5_backend_isolation(conn: sqlite3.Connection, db_path) -> tuple[bool, str]:
    """Compatibility alias for the old backend-isolation helper.

    The design-document H5 is :func:`h5_validity_order`; backend isolation is
    H8 and is now reported under that ID by :func:`run_all`.
    """
    return h8_no_cross_backend_leakage(conn, db_path)


def h6_rule_authority(conn: sqlite3.Connection) -> tuple[bool, str]:
    """H6: no rule below minimum validity enters runtime lifecycle."""
    n_rules = _count(conn, "tehm_rules")
    n_status = _count(conn, "tehm_rule_status")
    if n_rules == 0 and n_status == 0:
        return True, "not_applicable: no rules yet"
    invalid = conn.execute(
        """SELECT r.rule_id, r.validity_status FROM tehm_rules r
           JOIN tehm_rule_status rs ON rs.rule_id = r.rule_id
           WHERE r.validity_status NOT IN ('PROVISIONAL_VALID', 'VALIDATED')"""
    ).fetchall()
    if invalid:
        return False, f"rules below validity entered lifecycle: {[i['rule_id'] for i in invalid]}"
    return True, f"{n_rules} rules, {n_status} lifecycle rows (validity-gated)"


def h7_activation_honesty(conn: sqlite3.Connection) -> tuple[bool, str]:
    """H7: a PASS activation must show complete obligation evidence."""
    rows = conn.execute(
        """SELECT activation_id, outcome, obligation_coverage,
                  obligation_transfer_json, verifier_json, verification_status
           FROM tehm_activations"""
    ).fetchall()
    if not rows:
        return True, "not_applicable: no activations"
    bad: list[str] = []
    for row in rows:
        if row["outcome"] != "PASS" and row["verification_status"] != "PASS":
            continue
        prefix = row["activation_id"]
        transfer = tehm_db.read_json(row["obligation_transfer_json"])
        verifier = tehm_db.read_json(row["verifier_json"])
        if (row["obligation_coverage"] is None or
                float(row["obligation_coverage"]) < 1.0 or
                float(transfer.get("obligation_coverage", 0.0)) < 1.0 or
                float(verifier.get("obligation_coverage", 0.0)) < 1.0):
            bad.append(f"{prefix}:incomplete-obligation-coverage")
        statuses = [r.get("status") for r in transfer.get("results", [])
                    if isinstance(r, dict)]
        if any(status not in {"BOUND", "PASS"} for status in statuses):
            bad.append(f"{prefix}:unverified-obligation-result")
        for item in transfer.get("results", []):
            if item.get("status") == "PASS" and not item.get("evidence_refs"):
                bad.append(f"{prefix}:pass-obligation-without-evidence")
    if bad:
        return False, f"{len(bad)} activation honesty failures: {bad[:5]}"
    return True, f"{len(rows)} activations preserve obligation honesty"


def h8_no_cross_backend_leakage(conn: sqlite3.Connection, db_path) -> tuple[bool, str]:
    """H8: TEHM store/path cannot contain or resolve legacy authority."""
    name = str(db_path).lower()
    if "knowledge.sqlite" in name or "signoff-loop" in name:
        return False, f"TEHM db path must be isolated from legacy: {db_path}"
    # The schema must not carry legacy table names.
    legacy_tables = [r["name"] for r in conn.execute(
        """SELECT name FROM sqlite_master WHERE type='table' AND name IN
           ('runs', 'failure_events', 'fix_events', 'recipe_status', 'ab_trials')""")]
    if legacy_tables:
        return False, f"legacy tables present in TEHM store: {legacy_tables}"
    return True, "isolated TEHM store; no legacy authority present"


def h9_evaluation_firewall(conn: sqlite3.Connection, firewall: dict | None) -> tuple[bool, str]:
    """H9: held-out/A-B lineages never enter learner support."""
    if not firewall:
        return True, "not_applicable: no evaluation firewall manifest"
    training = set(firewall.get("training_lineages") or [])
    heldout = set(firewall.get("heldout_lineages") or [])
    if training & heldout:
        return False, f"training/held-out overlap: {sorted(training & heldout)}"
    excluded = heldout | set(firewall.get("ab_lineages") or [])
    if not excluded:
        return True, "not_applicable: empty held-out/A-B lineage set"
    bad: list[str] = []
    # The canonical store may retain excluded evidence for audit.  Only an
    # explicit learner-eligible membership is a firewall violation.
    rows = conn.execute(
        """SELECT DISTINCT e.episode_id, e.lineage_id
             FROM tehm_episodes e
             JOIN tehm_episode_steps es ON es.episode_id=e.episode_id
             JOIN tehm_dataset_membership dm ON dm.transition_id=es.transition_id
            WHERE dm.learner_eligible=1 AND e.lineage_id IS NOT NULL"""
    ).fetchall()
    bad += [f"episode:{r['episode_id']}:{r['lineage_id']}" for r in rows
            if r["lineage_id"] in excluded]
    rows = conn.execute(
        """SELECT rs.rule_id, rs.episode_id, e.lineage_id,
                         authority.status AS authority_status
           FROM tehm_rule_sources rs JOIN tehm_episodes e ON e.episode_id=rs.episode_id
           LEFT JOIN tehm_rule_status authority
             ON authority.rule_id=rs.rule_id
           WHERE e.lineage_id IS NOT NULL"""
    ).fetchall()
    bad += [f"rule-source:{r['rule_id']}:{r['lineage_id']}" for r in rows
            if r["lineage_id"] in excluded and
            r["authority_status"]
            not in {"retired", "demoted", "quarantined"}]
    if bad:
        return False, f"{len(bad)} held-out/A-B support leaks: {bad[:5]}"
    return True, f"firewall clean: {len(training)} training, {len(heldout)} held-out"


def h10_rollback_authority(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Every real ORFS trial has activation-linked, verified rollback evidence."""
    external = []
    for row in conn.execute(
            "SELECT trial_uuid, metrics_json FROM tehm_trials").fetchall():
        metrics = tehm_db.read_json(row["metrics_json"])
        if metrics.get("executor_version", "").startswith("orfs-trial-"):
            external.append((row["trial_uuid"], metrics))
    if not external:
        return True, "not_applicable: no real ORFS TEHM trials"
    bad = []
    for trial_uuid, metrics in external:
        if not metrics.get("rollback_verified"):
            bad.append(f"{trial_uuid}:trial-rollback-unverified")
        if not (metrics.get("registry_authority") or {}).get("verified"):
            bad.append(f"{trial_uuid}:registry-rollback-unverified")
        activations = conn.execute(
            "SELECT rollback_receipt_json FROM tehm_activations "
            "WHERE trial_uuid=?", (trial_uuid,)).fetchall()
        if not activations:
            bad.append(f"{trial_uuid}:no-activation-rollback")
        for activation in activations:
            receipt = tehm_db.read_json(activation["rollback_receipt_json"])
            if not receipt.get("verified"):
                bad.append(f"{trial_uuid}:activation-rollback-unverified")
    if bad:
        return False, f"{len(bad)} rollback authority failures: {bad[:5]}"
    return True, f"{len(external)} real ORFS trial(s) have verified rollback receipts"


def h12_no_silent_fallback(conn: sqlite3.Connection, db_path) -> tuple[bool, str]:
    """TEHM must fail closed instead of silently falling back to legacy."""
    if not str(db_path):
        return False, "db_path is empty"
    if "knowledge.sqlite" in str(db_path).lower():
        return False, f"cannot run TEHM honesty against the legacy DB: {db_path}"
    return True, "fail-closed backend; no silent legacy fallback"


def h11_deterministic_bundle(bundle_path: str | Path | None) -> tuple[bool, str]:
    """H11: an evidence bundle must pass deterministic sync verification."""
    if bundle_path is None:
        return True, "not_applicable: no evidence bundle supplied"
    try:
        from tehm.sync import verify_bundle
        result = verify_bundle(Path(bundle_path))
    except Exception as exc:  # noqa: BLE001
        return False, f"bundle verification crashed: {exc!r}"
    if not result.get("ok"):
        return False, result.get("detail", "bundle verification failed")
    return True, "deterministic bundle manifest and file digests verified"


HARD_CHECKS = (
    ("H1", h1_transition_completeness),
    ("H2", h2_view_provenance),
    ("H3", h3_unknown_not_false),
    ("H4", h4_source_witness_integrity),
    ("H5", h5_validity_order),
    ("H6", h6_rule_authority),
    ("H7", h7_activation_honesty),
    ("H8", h8_no_cross_backend_leakage),
    ("H9", h9_evaluation_firewall),
    ("H10", h10_rollback_authority),
    ("H11", h11_deterministic_bundle),
    ("H12", h12_no_silent_fallback),
)

SUPPLEMENTAL_CHECKS = (("A1", artifact_digest_integrity),)


def run_all(conn: sqlite3.Connection, store, db_path, *, firewall: dict | None = None,
            bundle_path: str | Path | None = None) -> tuple[bool, dict]:
    """Run H1--H12 plus supplemental checks.

    ``firewall`` and ``bundle_path`` are explicit inputs so a generic local
    database can report those gates as not-applicable, while a frozen evidence
    bundle must supply and verify them.
    """
    report: dict = {}
    for name, fn in HARD_CHECKS + SUPPLEMENTAL_CHECKS:
        try:
            if name == "H8":
                ok, detail = fn(conn, db_path)
            elif name == "H9":
                ok, detail = fn(conn, firewall)
            elif name == "H11":
                ok, detail = fn(bundle_path)
            elif name == "A1":
                ok, detail = fn(conn, store)
            elif name in ("H12",):
                ok, detail = fn(conn, db_path)
            else:
                ok, detail = fn(conn)
        except Exception as exc:  # noqa: BLE001 - a gate crash is a gate failure
            ok, detail = False, f"gate crashed: {exc!r}"
        report[name] = {"ok": ok, "detail": detail}
    all_ok = all(report[h]["ok"] for h, _ in HARD_CHECKS + SUPPLEMENTAL_CHECKS)
    return all_ok, report


# Backward-compatible names used by the existing unit tests and callers.
h4_artifact_digest_integrity = artifact_digest_integrity
