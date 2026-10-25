"""Physical Effect Memory (design doc 26 Phase 11).

Stores the observed physical deltas of executed actions and aggregates them per
action (transformation family / effect group). ``predict`` returns the EMPIRICAL
mean + support of the observed distribution — an honest first-phase memory that
does NOT claim a differentiable gradient (design doc Phase 11).
"""
from __future__ import annotations

import sqlite3
import statistics
import hashlib
import math

from tehm import db as tehm_db
from tehm.physical.effects import PHYSICAL_METRICS, PhysicalEffect, extract_deltas
from tehm.ids import stable_dumps

PHYSICAL_MEMORY_VERSION = "physical-effect-memory-v0.3"


class PhysicalEffectMemory:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- write ---------------------------------------------------------------

    def record(self, *, transition_id: str, action_domain: str,
               transformation_family: str, before_ppa: dict, after_ppa: dict,
               effect_key: str = "", domain: str = "flow.signoff",
               evidence_refs: list | None = None,
               graph_context=None, commit: bool = True) -> PhysicalEffect:
        """Record one observed physical effect (deltas computed from PPA)."""
        requested_graph_context = graph_context
        if graph_context is None:
            existing = self.conn.execute(
                "SELECT graph_context_json,graph_context_digest "
                "FROM tehm_physical_effects "
                "WHERE transition_id=?", (transition_id,)).fetchone()
            # ``{}`` is the serialized marker for "no graph context".  It is
            # not evidence and must never be re-hashed into a seemingly valid
            # context digest on an idempotent re-import.
            if (existing is not None and existing["graph_context_digest"] and
                    existing["graph_context_json"]):
                graph_context = tehm_db.read_json(existing["graph_context_json"])
        context, context_digest, extractor_version = _context_parts(graph_context)
        effect = PhysicalEffect(
            transition_id=transition_id,
            action_domain=action_domain,
            transformation_family=transformation_family,
            effect_key=effect_key,
            domain=domain,
            deltas=extract_deltas(before_ppa, after_ppa),
            before_ppa=before_ppa,
            after_ppa=after_ppa,
            evidence_refs=list(evidence_refs or []),
            graph_context=context,
            graph_context_digest=context_digest,
            graph_extractor_version=extractor_version,
        )
        row = effect.to_row()
        existing = self.conn.execute(
            "SELECT * FROM tehm_physical_effects WHERE transition_id=?",
            (transition_id,)).fetchone()
        if existing is not None:
            # A physical effect is evidence derived from one immutable
            # transition.  ``INSERT OR REPLACE`` used to let a retry silently
            # overwrite PPA/delta/provenance fields, invalidating the raw
            # evidence fingerprint used by anti-forgetting audits.  Replays
            # are idempotent; conflicting payloads fail closed.
            comparable = (
                "action_domain", "transformation_family", "domain",
                "before_ppa_json", "after_ppa_json", "deltas_json",
                "evidence_refs_json",
            )
            conflicts = [name for name in comparable
                         if str(existing[name] or "") != str(row[name] or "")]
            # An older row may have been recorded before its canonical effect
            # key was known.  Filling that single derived label is safe, but
            # changing a non-empty key is not.
            old_key, new_key = str(existing["effect_key"] or ""), str(row["effect_key"] or "")
            if old_key and old_key != new_key:
                conflicts.append("effect_key")
            if conflicts:
                raise ValueError(
                    "physical effect evidence is immutable and conflicts: "
                    + ",".join(conflicts))
            old_graph = str(existing["graph_context_digest"] or "")
            new_graph = str(row["graph_context_digest"] or "")
            if old_graph and old_graph != new_graph:
                raise ValueError(
                    "physical effect graph context is immutable and conflicts")
            key_changed = old_key != new_key
            if old_graph == new_graph and not key_changed:
                return effect
            # Only enrich a row that had no graph context.  Explicitly passing
            # an empty context must not erase a previously bound context.
            if requested_graph_context is not None and not new_graph:
                raise ValueError(
                    "physical effect graph context cannot be removed")
            self.conn.execute(
                "UPDATE tehm_physical_effects SET effect_key=?, "
                "graph_context_json=?, graph_context_digest=?, "
                "graph_extractor_version=? WHERE transition_id=?",
                (new_key or old_key, row["graph_context_json"], new_graph,
                 row["graph_extractor_version"], transition_id))
            if commit:
                self.conn.commit()
            return effect
        self.conn.execute(
            """INSERT INTO tehm_physical_effects (
                   transition_id, action_domain, transformation_family,
                   effect_key, domain, before_ppa_json, after_ppa_json,
                   deltas_json, evidence_refs_json, graph_context_json,
                   graph_context_digest, graph_extractor_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["transition_id"], row["action_domain"],
             row["transformation_family"], row["effect_key"], row["domain"],
             row["before_ppa_json"], row["after_ppa_json"],
             row["deltas_json"], row["evidence_refs_json"],
             row["graph_context_json"], row["graph_context_digest"],
             row["graph_extractor_version"],
             tehm_db.now_local()))
        # Callers that import a batch or compose this write with canonical
        # capture can keep the effect inside their own savepoint.  Preserve
        # the historical default commit behavior for standalone callers.
        if commit:
            self.conn.commit()
        return effect

    def backfill_ppa(self, transition_id: str, *, before_ppa: dict,
                     after_ppa: dict, evidence_refs: list | None = None,
                     replace: bool = False) -> dict:
        """Re-extract PPA from preserved evidence without changing transition ID."""
        row = self.conn.execute(
            "SELECT before_ppa_json,after_ppa_json,evidence_refs_json "
            "FROM tehm_physical_effects WHERE transition_id=?",
            (transition_id,)).fetchone()
        if row is None:
            raise KeyError(f"physical effect not found: {transition_id}")
        current_before = tehm_db.read_json(row["before_ppa_json"])
        current_after = tehm_db.read_json(row["after_ppa_json"])
        if not replace and ((current_before and current_before != before_ppa) or
                            (current_after and current_after != after_ppa)):
            raise ValueError("physical effect already has different PPA evidence")
        refs = tehm_db.read_json(row["evidence_refs_json"], default=[])
        refs = refs if isinstance(refs, list) else []
        for ref in evidence_refs or []:
            if ref not in refs:
                refs.append(ref)
        deltas = extract_deltas(before_ppa, after_ppa)
        self.conn.execute(
            "UPDATE tehm_physical_effects SET before_ppa_json=?,after_ppa_json=?,"
            "deltas_json=?,evidence_refs_json=? WHERE transition_id=?",
            (stable_dumps(before_ppa), stable_dumps(after_ppa),
             stable_dumps(deltas), stable_dumps(refs), transition_id))
        self.conn.commit()
        return deltas

    def attach_graph_context(self, transition_id: str, graph_context, *,
                             replace: bool = False) -> str:
        """Backfill a provenance-bound def-graph context onto one observation."""
        context, digest, version = _context_parts(graph_context)
        if not digest:
            raise ValueError("graph context must be non-empty")
        row = self.conn.execute(
            "SELECT graph_context_digest FROM tehm_physical_effects "
            "WHERE transition_id=?", (transition_id,)).fetchone()
        if row is None:
            raise KeyError(f"physical effect not found: {transition_id}")
        current = row["graph_context_digest"]
        if current and current != digest and not replace:
            raise ValueError(
                f"physical effect already has different graph context: {current}")
        self.conn.execute(
            "UPDATE tehm_physical_effects SET graph_context_json=?, "
            "graph_context_digest=?, graph_extractor_version=? "
            "WHERE transition_id=?",
            (stable_dumps(context), digest, version, transition_id))
        self.conn.commit()
        return digest

    # -- read ----------------------------------------------------------------

    def profile(self, *, family: str | None = None,
                effect_key: str | None = None,
                graph_context_digest: str | None = None) -> dict:
        """Empirical physical-effect profile of one action (mean/min/max deltas)."""
        clauses, params = [], []
        if family:
            clauses.append("transformation_family = ?")
            params.append(family)
        if effect_key:
            clauses.append("effect_key = ?")
            params.append(effect_key)
        if graph_context_digest:
            clauses.append("graph_context_digest = ?")
            params.append(graph_context_digest)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT transition_id, transformation_family, effect_key, "
            f"action_domain, deltas_json, evidence_refs_json, "
            f"graph_context_json, graph_context_digest "
            f"FROM tehm_physical_effects{where}", params).fetchall()
        if not rows:
            return {"family": family, "effect_key": effect_key,
                    "support": 0, "mean_deltas": {}, "min_deltas": {},
                    "max_deltas": {}, "harmful_metrics": [],
                    "graph_context_support": 0,
                    "unique_graph_contexts": 0,
                    "context_filter": graph_context_digest}

        per_metric: dict[str, list[float]] = {m: [] for m in PHYSICAL_METRICS}
        for r in rows:
            deltas = tehm_db.read_json(r["deltas_json"])
            for metric in PHYSICAL_METRICS:
                value = deltas.get(metric)
                if value is not None:
                    per_metric[metric].append(float(value))

        mean_deltas = {m: (round(statistics.mean(v), 6) if v else None)
                       for m, v in per_metric.items()}
        min_deltas = {m: (round(min(v), 6) if v else None)
                      for m, v in per_metric.items()}
        max_deltas = {m: (round(max(v), 6) if v else None)
                      for m, v in per_metric.items()}
        # harmful signals: metrics that, on average, move in the wrong direction
        harmful = _harmful_signals(mean_deltas, per_metric)
        context_digests = [r["graph_context_digest"] for r in rows
                           if r["graph_context_digest"]]
        return {
            "family": family, "effect_key": effect_key,
            "support": len(rows),
            "mean_deltas": mean_deltas,
            "min_deltas": min_deltas,
            "max_deltas": max_deltas,
            "harmful_metrics": harmful,
            "graph_context_support": len(context_digests),
            "unique_graph_contexts": len(set(context_digests)),
            "context_filter": graph_context_digest,
            "note": ("empirical physical-effect memory (Phase 11); no "
                     "differentiable gradient claimed"),
        }

    def predict(self, *, family: str, effect_key: str | None = None,
                graph_context_digest: str | None = None, graph_context=None,
                k: int = 5, min_unique_contexts: int = 3,
                max_distance: float = 3.0,
                calibration_policy: dict | None = None,
                action: dict | None = None) -> dict:
        """Predict an empirical effect, optionally by similar physical graphs.

        ``graph_context_digest`` preserves the v0.1 exact-context API.  Supplying
        ``graph_context`` enables calibrated k-nearest-context retrieval.  It is
        deliberately conservative: platform and dataset tier must match, repeat
        observations of one graph count as one geometric neighbour, and sparse or
        out-of-distribution queries abstain instead of inventing a gradient.
        When ``action`` is supplied, the read path additionally requires the
        stored transition action to have the same domain, family, config-edit
        keys, and normalized config-edit values; unknown action provenance is
        never treated as compatible evidence.
        """
        if graph_context is None:
            return self.profile(family=family, effect_key=effect_key,
                                graph_context_digest=graph_context_digest)
        context, digest, _ = _context_parts(graph_context)
        return self._predict_similar(
            family=family, effect_key=effect_key, query=context,
            query_digest=digest, k=max(1, int(k)),
            min_unique_contexts=max(2, int(min_unique_contexts)),
            max_distance=float(max_distance),
            calibration_policy=calibration_policy, action=action)

    def _predict_similar(self, *, family: str, effect_key: str | None,
                         query: dict, query_digest: str, k: int,
                         min_unique_contexts: int, max_distance: float,
                         calibration_policy: dict | None,
                         action: dict | None) -> dict:
        clauses, params = ["transformation_family = ?"], [family]
        if effect_key:
            clauses.append("effect_key = ?")
            params.append(effect_key)
        rows = self.conn.execute(
            "SELECT p.transition_id,p.deltas_json,p.graph_context_json,"
            "p.graph_context_digest,t.action_json "
            "FROM tehm_physical_effects p "
            "LEFT JOIN tehm_transitions t ON t.transition_id=p.transition_id "
            "WHERE " + " AND ".join("p." + clause for clause in clauses),
            params).fetchall()
        base = {
            "family": family, "effect_key": effect_key,
            "query_graph_context_digest": query_digest,
            "retrieval_mode": "similar_graph_knn",
            "memory_version": PHYSICAL_MEMORY_VERSION,
            "gradient_claimed": False,
            "platform": str(query.get("platform") or ""),
            "dataset_tier": str(query.get("dataset_tier") or ""),
        }
        action_signature = _action_signature(action) if action is not None else None
        if action is not None and action_signature is None:
            return _abstention(base, "invalid_action_signature")
        base["action_conditioned"] = action_signature is not None
        base["action_signature"] = action_signature
        calibration = None
        if calibration_policy is not None:
            calibration = dict(calibration_policy)
            if str(calibration.get("family") or "") != family:
                return _abstention(
                    base, "calibration_family_mismatch",
                    calibration_status=calibration.get("status"))
            if calibration.get("status") != "ready":
                return _abstention(
                    base, "heldout_calibration_not_ready",
                    calibration_status=calibration.get("status"),
                    calibration_reason=calibration.get("reason"))
            policy_action_signature = calibration.get("action_signature")
            if action_signature is not None and policy_action_signature is None:
                return _abstention(
                    base, "calibration_action_signature_unbound",
                    calibration_status=calibration.get("status"))
            if policy_action_signature is not None:
                if action_signature is None:
                    return _abstention(
                        base, "calibration_action_signature_required",
                        calibration_status=calibration.get("status"),
                        expected_action_signature=policy_action_signature)
                if action_signature != policy_action_signature:
                    return _abstention(
                        base, "calibration_action_signature_mismatch",
                        calibration_status=calibration.get("status"),
                        expected_action_signature=policy_action_signature)
            thresholds = calibration.get("thresholds") or {}
            calibrated_distance = thresholds.get("max_distance")
            if not isinstance(calibrated_distance, (int, float)) or not math.isfinite(
                    float(calibrated_distance)):
                return _abstention(
                    base, "missing_calibrated_distance_threshold",
                    calibration_status=calibration.get("status"))
            max_distance = min(max_distance, float(calibrated_distance))
            min_unique_contexts = max(
                min_unique_contexts,
                max(2, int(thresholds.get("min_unique_contexts") or 0)))
        query_vector = _numeric_graph_features(query)
        if not query_vector:
            return _abstention(base, "missing_query_features")
        if not base["platform"]:
            return _abstention(base, "missing_query_platform")
        if not base["dataset_tier"]:
            return _abstention(base, "missing_query_dataset_tier")

        # Group repeats by content digest before distance calculation.  Repeating
        # one DEF may improve its delta estimate, but never fabricates geometric
        # coverage or lets one design dominate kNN support.
        contexts: dict[str, dict] = {}
        incompatible_platform = incompatible_tier = 0
        incompatible_action = unknown_action = 0
        for row in rows:
            context = tehm_db.read_json(row["graph_context_json"])
            digest = str(row["graph_context_digest"] or "")
            if not digest or not isinstance(context, dict):
                continue
            if action_signature is not None:
                observed_signature = _action_signature(
                    tehm_db.read_json(row["action_json"]))
                if observed_signature is None:
                    unknown_action += 1
                    continue
                if observed_signature != action_signature:
                    incompatible_action += 1
                    continue
            if str(context.get("platform") or "") != base["platform"]:
                incompatible_platform += 1
                continue
            if str(context.get("dataset_tier") or "") != base["dataset_tier"]:
                incompatible_tier += 1
                continue
            vector = _numeric_graph_features(context)
            if not vector:
                continue
            item = contexts.setdefault(
                digest, {"context": context, "vector": vector, "deltas": [],
                         "transitions": []})
            item["deltas"].append(tehm_db.read_json(row["deltas_json"]))
            item["transitions"].append(row["transition_id"])

        if action_signature is not None:
            base["incompatible_action"] = incompatible_action
            base["unknown_action_metadata"] = unknown_action

        if not contexts:
            # Tier mismatch is more specific: it proves same-platform evidence
            # existed but was intentionally firewalled (research vs strict).
            reason = ("no_action_compatible_contexts"
                      if action_signature is not None and
                      (incompatible_action or unknown_action)
                      else "no_dataset_tier_compatible_contexts" if incompatible_tier
                      else "no_platform_compatible_contexts" if incompatible_platform
                      else "no_graph_context_evidence")
            return _abstention(base, reason,
                               incompatible_platform=incompatible_platform,
                               incompatible_tier=incompatible_tier,
                               incompatible_action=incompatible_action,
                               unknown_action_metadata=unknown_action)
        if len(contexts) < min_unique_contexts:
            return _abstention(
                base, "insufficient_unique_contexts",
                unique_contexts=len(contexts), required_unique_contexts=min_unique_contexts,
                incompatible_platform=incompatible_platform,
                incompatible_tier=incompatible_tier,
                incompatible_action=incompatible_action,
                unknown_action_metadata=unknown_action)

        distances = _context_distances(query_vector, contexts)
        ranked = sorted((distance, digest, contexts[digest])
                        for digest, distance in distances.items())
        if not ranked or ranked[0][0] > max_distance:
            return _abstention(
                base, "out_of_distribution", unique_contexts=len(contexts),
                nearest_distance=(round(ranked[0][0], 6) if ranked else None),
                max_distance=max_distance)
        neighbours = ranked[:min(k, len(ranked))]
        summaries = []
        for distance, digest, item in neighbours:
            summaries.append({
                "digest": digest, "distance": distance,
                "weight": 1.0 / max(0.05, distance + 0.05),
                "deltas": _mean_delta(item["deltas"]),
                "observation_support": len(item["deltas"]),
                "transition_ids": sorted(item["transitions"]),
            })
        prediction, uncertainty = _weighted_effect(summaries)
        # A ready policy may carry split-conformal residual quantiles learned
        # from independent held-out lineages.  Apply those intervals only when
        # explicitly present; legacy policies retain the v0.3 weighted-mean
        # interval and are never silently reinterpreted.
        if calibration is not None:
            _apply_conformal_intervals(prediction, uncertainty, calibration)
        metric_support = {metric: detail["context_support"]
                          for metric, detail in uncertainty.items()}
        supported = [m for m, n in metric_support.items()
                     if n >= min_unique_contexts]
        if not supported:
            return _abstention(
                base, "insufficient_metric_support", unique_contexts=len(contexts),
                neighbour_contexts=len(summaries), metric_support=metric_support)
        # Metrics below the support threshold remain explicitly unavailable.
        for metric in PHYSICAL_METRICS:
            if metric not in supported:
                prediction[metric] = None
                uncertainty[metric]["lower_95"] = None
                uncertainty[metric]["upper_95"] = None

        calibration_summary = None
        if calibration is not None:
            calibration_status = calibration.get("status")
            if calibration_status is not None and calibration_status != "ready":
                return _abstention(
                    base, "heldout_calibration_not_ready",
                    calibration_status=calibration_status,
                    calibration_reason=calibration.get("reason"))
            thresholds = calibration.get("thresholds") or {}
            calibration_data = calibration.get("calibration") or {}
            required_coverage = thresholds.get("required_coverage")
            empirical_coverage = calibration_data.get("empirical_coverage")
            if not all(isinstance(value, (int, float))
                       for value in (required_coverage, empirical_coverage)):
                return _abstention(
                    base, "missing_calibrated_coverage_threshold",
                    calibration_status=calibration.get("status"))
            if float(empirical_coverage) < float(required_coverage):
                return _abstention(
                    base, "calibrated_coverage_below_threshold",
                    empirical_coverage=empirical_coverage,
                    required_coverage=required_coverage)
            required_metrics = set(calibration_data.get("required_metrics") or [])
            width_limits = thresholds.get("max_uncertainty_widths") or {}
            if not required_metrics:
                return _abstention(base, "no_calibrated_metrics")
            missing_metrics = sorted(required_metrics - set(supported))
            if missing_metrics:
                return _abstention(
                    base, "insufficient_calibrated_metric_support",
                    missing_calibrated_metrics=missing_metrics)
            excessive = {}
            for metric in sorted(required_metrics):
                interval = uncertainty.get(metric) or {}
                lower, upper = interval.get("lower_95"), interval.get("upper_95")
                limit = width_limits.get(metric)
                if not all(isinstance(value, (int, float))
                           for value in (lower, upper, limit)):
                    return _abstention(
                        base, "missing_calibrated_uncertainty_threshold",
                        metric=metric)
                width = float(upper) - float(lower)
                if width > float(limit) + 1.0e-12:
                    excessive[metric] = {
                        "interval_width": round(width, 6),
                        "max_interval_width": round(float(limit), 6),
                    }
            if excessive:
                return _abstention(
                    base, "prediction_uncertainty_above_threshold",
                    excessive_uncertainty=excessive)
            # Never expose an uncalibrated metric as though it passed the
            # held-out uncertainty gate.
            for metric in set(PHYSICAL_METRICS) - required_metrics:
                prediction[metric] = None
                uncertainty[metric]["lower_95"] = None
                uncertainty[metric]["upper_95"] = None
            calibration_summary = {
                "version": calibration.get("version"),
                "status": calibration.get("status"),
                "heldout_lineages": (calibration.get("firewall") or {}).get(
                    "heldout_lineages", []),
                "empirical_coverage": empirical_coverage,
                "required_coverage": required_coverage,
                "required_metrics": sorted(required_metrics),
            }

        return {
            **base, "abstained": False, "abstain_reasons": [],
            "support": sum(x["observation_support"] for x in summaries),
            "unique_graph_contexts": len(contexts),
            "neighbour_contexts": len(summaries),
            "nearest_distance": round(summaries[0]["distance"], 6),
            "max_distance": max_distance,
            "calibration": calibration_summary,
            "mean_deltas": prediction,
            "uncertainty_95": uncertainty,
            "harmful_metrics": _harmful_signals(
                prediction, {m: [prediction[m]] if prediction.get(m) is not None else []
                             for m in PHYSICAL_METRICS}),
            "neighbours": [{k: (round(v, 6) if isinstance(v, float) else v)
                             for k, v in item.items() if k != "deltas"}
                            for item in summaries],
            "note": ("weighted empirical effect over compatible physical graph "
                     "contexts; uncertainty is a weighted 95% mean interval; "
                     "no gradient or cross-context causal effect claimed"),
        }

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0])


def _harmful_signals(mean_deltas: dict, per_metric: dict) -> list:
    """Metrics whose mean delta is harmful (timing/area/power/DRC moving up)."""
    harmful: list = []
    for metric in ("wns_ns", "tns_ns"):
        value = mean_deltas.get(metric)
        if value is not None and value < 0:
            harmful.append(metric)
    for metric in ("area_um2", "power_w", "congestion", "drc_violations"):
        value = mean_deltas.get(metric)
        if value is not None and value > 0:
            harmful.append(metric)
    return harmful


def _action_signature(action: dict | None) -> dict | None:
    """Extract the stable, observable part of a typed flow action.

    Physical effects are stored against transition IDs rather than duplicating
    the full action payload.  Shadow prediction may therefore request a
        read-only compatibility check against the transition's action JSON.  The
        signature includes domain, family, edit keys, and normalized edit values:
        a different numeric knob value is a different support population and
        must fail closed until it has its own calibration evidence.
    """
    if not isinstance(action, dict):
        return None
    payload = action.get("payload")
    if not isinstance(payload, dict):
        payload = action
    edits = payload.get("config_edits")
    if not isinstance(edits, dict) or not edits:
        return None
    domain = action.get("domain") or payload.get("domain")
    family = action.get("transformation_family") or payload.get(
        "transformation_family")
    if not isinstance(domain, str) or not domain:
        return None
    if not isinstance(family, str) or not family:
        return None
    typed = _typed_action_signature(action, payload=payload, edits=edits,
                                    domain=domain, family=family)
    # A partially supplied typed action is unsafe: silently treating it as an
    # exact config edit would erase the promised knob/direction/operation-point
    # boundary.  Legacy actions with none of the typed fields remain supported
    # through the exact signature below.
    typed_fields = {
        "knob", "direction", "relative_change", "relative_change_pct",
        "operation_point",
    }
    if any(key in action or key in payload for key in typed_fields) and typed is None:
        return None
    return {
        "domain": domain,
        "transformation_family": family,
        "config_edit_keys": sorted(str(key) for key in edits),
        "config_edit_values": {
            str(key): edits[key] for key in sorted(edits, key=str)
        },
        "typed_action": typed,
    }


def typed_action_signature(action: dict | None) -> dict | None:
    """Return the validated typed-action signature used by shadow retrieval.

    A typed action is intentionally stricter than the legacy exact config
    signature.  It must identify one knob, a direction, a finite relative
    change, and an operation point.  Exact config edits remain part of the
    resulting signature, so a typed description can never borrow evidence for
    another concrete value by accident.  ``None`` means malformed or legacy
    untyped input; callers should use :func:`_action_signature` when they need
    the backward-compatible exact signature.
    """
    if not isinstance(action, dict):
        return None
    payload = action.get("payload")
    payload = payload if isinstance(payload, dict) else action
    edits = payload.get("config_edits")
    domain = action.get("domain") or payload.get("domain")
    family = action.get("transformation_family") or payload.get(
        "transformation_family")
    return _typed_action_signature(action, payload=payload, edits=edits,
                                   domain=domain, family=family)


def _typed_action_signature(action: dict, *, payload: dict, edits,
                            domain, family) -> dict | None:
    if not isinstance(edits, dict) or not edits:
        return None
    typed_present = any(key in action or key in payload for key in (
        "knob", "direction", "relative_change", "relative_change_pct",
        "operation_point"))
    if not typed_present:
        return None
    if not isinstance(domain, str) or not domain:
        return None
    if not isinstance(family, str) or not family:
        return None
    knob = payload.get("knob", action.get("knob"))
    if knob is None and len(edits) == 1:
        knob = next(iter(edits))
    if not isinstance(knob, str) or not knob or knob not in edits:
        return None
    direction = payload.get("direction", action.get("direction"))
    if not isinstance(direction, str):
        return None
    direction = direction.strip().lower().replace("-", "_")
    if direction not in {"increase", "decrease", "absolute",
                         "increase_or_equal", "decrease_or_equal"}:
        return None
    relative = payload.get("relative_change", action.get("relative_change"))
    if relative is None:
        relative = payload.get("relative_change_pct",
                               action.get("relative_change_pct"))
        if relative is not None:
            try:
                relative = float(relative) / 100.0
            except (TypeError, ValueError):
                return None
    try:
        relative = float(relative)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(relative):
        return None
    operation = payload.get("operation_point", action.get("operation_point"))
    if operation is None or operation == "":
        return None
    if not isinstance(operation, (str, int, float, dict, list, tuple, bool)):
        return None
    return {
        "version": "typed-action-v1",
        "knob": str(knob),
        "direction": direction,
        "relative_change": relative,
        "operation_point": _canonical_action_value(operation),
    }


def _canonical_action_value(value):
    """Make typed operation-point values deterministic and JSON-safe."""
    if isinstance(value, tuple):
        return [_canonical_action_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_action_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_action_value(value[key])
                for key in sorted(value, key=str)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("typed action contains a non-finite value")
        return int(value) if value.is_integer() else value
    return value


def _context_parts(graph_context) -> tuple[dict, str, str]:
    if graph_context is None:
        return {}, "", ""
    if hasattr(graph_context, "to_dict"):
        payload = graph_context.to_dict()
    elif isinstance(graph_context, dict):
        payload = dict(graph_context)
    else:
        raise TypeError("graph_context must be PhysicalGraphContext, dict, or None")
    supplied_digest = str(payload.get("digest") or "")
    identity = {k: v for k, v in payload.items()
                if k not in {"digest", "source_refs"}}
    computed = hashlib.sha256(stable_dumps(identity).encode()).hexdigest()
    if supplied_digest and supplied_digest != computed:
        raise ValueError("graph context digest does not match its content")
    return payload, computed, str(payload.get("extractor_version") or "")


def _numeric_graph_features(context: dict) -> dict[str, float]:
    """Flatten the compact graph context into stable, finite numeric features."""
    result: dict[str, float] = {}
    for section in ("graph_features", "topology_rows"):
        values = context.get(section) or {}
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            # Counts span orders of magnitude; log scaling keeps die size or one
            # edge table from overwhelming all topology information.
            if section == "topology_rows" or key.startswith("num_") or key.endswith("_area"):
                number = math.copysign(math.log1p(abs(number)), number)
            result[f"{section}.{key}"] = number
    return result


def _context_distances(query: dict[str, float], contexts: dict[str, dict]) -> dict[str, float]:
    common = set(query)
    for item in contexts.values():
        common &= set(item["vector"])
    if not common:
        return {}
    scales: dict[str, float] = {}
    for key in common:
        values = [query[key], *(item["vector"][key] for item in contexts.values())]
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        mad = statistics.median(deviations) * 1.4826
        spread = max(values) - min(values)
        scale = mad if mad > 1e-12 else spread if spread > 1e-12 else 1.0
        # Counts are represented in log1p space above.  When a training slice
        # has nearly identical topology counts, its tiny spread is a poor
        # scale estimate and lets one unseen count dominate the whole metric
        # (for example, a 427-vs-1467 pin count becoming distance ~256).
        # Keep the natural one-log-unit floor for those dimensions only; the
        # hard OOD ceiling remains unchanged and continuous PPA/geometry
        # features retain their empirical robust scale.
        scale = max(_feature_scale_floor(key), scale)
        scales[key] = scale
    return {
        digest: math.sqrt(sum(
            ((item["vector"][key] - query[key]) / scales[key]) ** 2
            for key in common) / len(common))
        for digest, item in contexts.items()
    }


def _is_log_feature(key: str) -> bool:
    """Whether ``_numeric_graph_features`` encoded this key with log1p."""
    return (key.startswith("topology_rows.") or
            key.startswith("graph_features.num_") or
            key.endswith(".core_area"))


def _feature_scale_floor(key: str) -> float:
    """Return a conservative natural-unit floor for a feature scale."""
    if _is_log_feature(key):
        return 1.0
    # Fanout is a ratio and often differs by only a few hundredths across
    # related netlists; a tiny empirical spread would otherwise dominate.
    if key == "graph_features.avg_fanout":
        return 0.1
    return 0.0


def _mean_delta(rows: list[dict]) -> dict[str, float | None]:
    return {metric: (statistics.mean(values) if values else None)
            for metric in PHYSICAL_METRICS
            for values in [[float(row[metric]) for row in rows
                            if row.get(metric) is not None]]}


def _weighted_effect(neighbours: list[dict]) -> tuple[dict, dict]:
    prediction, uncertainty = {}, {}
    for metric in PHYSICAL_METRICS:
        values = [(item["weight"], item["deltas"].get(metric))
                  for item in neighbours if item["deltas"].get(metric) is not None]
        if not values:
            prediction[metric] = None
            uncertainty[metric] = {
                "lower_95": None, "upper_95": None, "std": None,
                "standard_error": None, "effective_contexts": 0.0,
                "context_support": 0}
            continue
        total = sum(weight for weight, _ in values)
        mean = sum(weight * value for weight, value in values) / total
        variance = sum(weight * (value - mean) ** 2 for weight, value in values) / total
        effective = total * total / sum(weight * weight for weight, _ in values)
        std = math.sqrt(max(0.0, variance))
        stderr = std / math.sqrt(effective) if effective > 0 else None
        half = 1.959963984540054 * stderr if stderr is not None else None
        prediction[metric] = round(mean, 6)
        uncertainty[metric] = {
            "lower_95": round(mean - half, 6) if half is not None else None,
            "upper_95": round(mean + half, 6) if half is not None else None,
            "std": round(std, 6),
            "standard_error": round(stderr, 6) if stderr is not None else None,
            "effective_contexts": round(effective, 6),
            "context_support": len(values),
        }
    return prediction, uncertainty


def _apply_conformal_intervals(prediction: dict, uncertainty: dict,
                               calibration: dict) -> None:
    """Replace normal-approximation bounds with frozen residual quantiles.

    The point estimate remains the weighted empirical mean.  A policy can opt
    into split-conformal bounds by carrying ``interval_method`` and
    ``thresholds.conformal_quantiles``; missing or invalid quantiles leave the
    corresponding metric unavailable rather than widening it heuristically.
    """
    if calibration.get("interval_method") != "split_conformal_residual_v1":
        return
    thresholds = calibration.get("thresholds") or {}
    quantiles = thresholds.get("conformal_quantiles") or {}
    if not isinstance(quantiles, dict):
        return
    for metric, quantile in quantiles.items():
        value = prediction.get(metric)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        if not isinstance(quantile, (int, float)) or not math.isfinite(float(quantile)):
            continue
        quantile = max(0.0, float(quantile))
        detail = uncertainty.get(metric)
        if not isinstance(detail, dict):
            continue
        detail["lower_95"] = round(float(value) - quantile, 6)
        detail["upper_95"] = round(float(value) + quantile, 6)
        detail["interval_method"] = "split_conformal_residual_v1"
        detail["conformal_radius"] = round(quantile, 6)


def _abstention(base: dict, reason: str, **details) -> dict:
    return {
        **base, "abstained": True, "abstain_reasons": [reason],
        "support": 0, "mean_deltas": {}, "uncertainty_95": {},
        "harmful_metrics": [], **details,
        "note": ("physical graph retrieval abstained; support, compatibility, "
                 "or distribution gate was not satisfied; no gradient claimed"),
    }
