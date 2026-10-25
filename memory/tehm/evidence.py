"""One evidence count for a rule, shared by runtime admission and rebuild (P1b).

Protocol v1 section 8.1-8.2; design memory/evaluation/r2g_P1b_test_design_20261008.md (D2 table).

Evidence unit: a rule's OWN applications, i.e. the transitions listed in its source substitutions
(``tehm_rule_sources.source_substitution_json``), whatever executed them (catalogue steps, LLM fixes,
component trials). Other steps of the source episodes are not the rule's evidence.

Canonical unit: one per physical attempt, keyed by the capture record id in the transition's provenance
(it names the session and iteration) and falling back to the transition id. A re-ingested copy of the same attempt
never counts twice.

Classes (one per canonical unit):
- ``positive``       PASS or PARTIAL, verified, and confirmed NOT rolled back (marker ``"false"``)
- ``rollback_unknown`` PASS or PARTIAL, verified, but whether it was rolled back is not recorded
                     (legacy records): never positive in the main caliber; counted as positive only in
                     the separate ``history`` caliber, for compatibility / sensitivity analyses
- ``harmful``        REGRESSION, or a verified FAIL that changed the target for the worse (or, diagnosed as an
                     infeasibility of the action, has no counts)
- ``no_clear``       NEUTRAL, or a verified FAIL with the target count unchanged (no improvement): never a success
- ``rolled_back``    a verified PASS/PARTIAL that was rolled back: never positive (kept, with its reason)
- ``no_evidence``    UNKNOWN, or not verified (oracle incomplete / not a REPAIR experiment), or a FAIL with no
                     measured after-count that is not diagnosed as the action's failure (a failed rerun: legacy
                     records never stored its cause): never positive, never negative (budget refusals,
                     accounting errors, timeouts, kills, infrastructure faults, unexplained crashes)

Calibers (user review 2026-10-09): ``main`` -- positive evidence requires an explicit "not rolled back"
(recorded by fix_signoff since P1b, or recovered from auditable raw records with the recovery basis
stored on the transition); ``history`` -- also counts ``rollback_unknown`` as positive, reproducing the
pre-P1b reading of legacy records. Select with TEHM_EVIDENCE_CALIBER (default ``main``).
"""
from __future__ import annotations

import json
import re
import sqlite3

CLASSES = ("positive", "harmful", "no_clear", "rolled_back", "rollback_unknown", "no_evidence")
# when several copies of one physical attempt disagree, the most conservative class wins
_PRECEDENCE = ("harmful", "no_evidence", "no_clear", "rolled_back", "rollback_unknown", "positive")
CALIBERS = ("main", "history")
_UNDIAGNOSED_CRASH = re.compile(r"flow_rerun_failed:rc\d+")


def _j(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify(outcome: str, verifier: dict, delta: dict) -> str:
    """Evidence class of one transition (see the module docstring)."""
    shared = ((verifier.get("input_binding") or {}).get("shared_history") or {})
    if shared and shared.get("schema") != "tehm-shared-label-evidence-v1":
        raise ValueError("unknown shared evidence schema")
    if shared.get("schema") == "tehm-shared-label-evidence-v1":
        cls = shared.get("evidence_class")
        if cls not in CLASSES:
            raise ValueError("invalid shared evidence class")
        if cls == "positive" and (shared.get("admitted") is not True
                or verifier.get("oracle_complete") is not True or verifier.get("verdict") != "PASS"
                or delta.get("experiment_kind") != "REPAIR" or delta.get("rolled_back") != "false"):
            raise ValueError("shared positive lacks admitted verified non-rollback evidence")
        return cls
    verified = verifier.get("oracle_complete") is True and delta.get("experiment_kind") == "REPAIR"
    if not verified or outcome == "UNKNOWN":
        return "no_evidence"
    regs = list(delta.get("created_regressions") or [])
    if regs and all(_UNDIAGNOSED_CRASH.fullmatch(str(r)) for r in regs):
        # captured between P1b-1 and the 2026-10-09 review: a rerun crash recorded as a flow regression
        # without its diagnosis -- not attributable to the action
        return "no_evidence"
    if outcome == "REGRESSION" or regs:
        return "harmful"
    if outcome == "NEUTRAL":
        return "no_clear"
    if outcome == "FAIL":
        fd = delta.get("first_divergence") or {}
        b, a = _num(fd.get("before")), _num(fd.get("after"))
        if a is None:
            # no measured after-count = the rerun itself failed (stage re-abort or crash). Negative only
            # when diagnosed as an infeasibility of the action (user review 2026-10-09); then the
            # stage-completion counts (failing_tests 1 -> 1: still aborting) decide no-change vs worse.
            rf = verifier.get("rerun_failure")
            if not (isinstance(rf, dict) and rf.get("attribution") == "action"):
                return "no_evidence"
            ft = delta.get("failing_tests") or {}
            b, a = _num(ft.get("before")), _num(ft.get("after"))
        return "no_clear" if (b is not None and a is not None and a == b) else "harmful"
    if outcome in ("PASS", "PARTIAL"):
        marker = delta.get("rolled_back", "UNKNOWN")
        return {"true": "rolled_back", "false": "positive"}.get(marker, "rollback_unknown")
    return "no_evidence"


def _unit_key(transition_id: str, provenance: dict) -> str:
    """One physical attempt: the capture record id (``r2g:<session>:iter<i>:<family>`` already names the
    session), as stored in transition provenance by canonical capture; else the transition id."""
    rid = provenance.get("record_id")
    return "rec:" + rid if rid else "tid:" + transition_id


def rule_evidence(conn: sqlite3.Connection, rule_id: str) -> dict:
    """Counts of the rule's canonical own units per class, plus audit fields."""
    own: set[str] = set()
    for (raw,) in conn.execute("SELECT source_substitution_json FROM tehm_rule_sources WHERE rule_id=?", (rule_id,)):
        own.update((_j(raw) or {}).keys())
    units: dict[str, list[str]] = {}
    has_attempts = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tehm_attempts'").fetchone() is not None
    for tid in sorted(own):
        row = conn.execute("SELECT outcome, verifier_json, observation_delta_json, provenance_json "
                           "FROM tehm_transitions WHERE transition_id=?", (tid,)).fetchone()
        if row is None:
            continue
        outcome, verifier, delta, prov = row[0], _j(row[1]), _j(row[2]), _j(row[3])
        cls = classify(outcome, verifier, delta)
        attempts = [r for (r,) in conn.execute("SELECT record_id FROM tehm_attempts WHERE transition_id=?", (tid,))] \
            if has_attempts else []
        for rid in attempts:               # P1c: every physical attempt with this content is its own unit
            units.setdefault("rec:" + rid, []).append(cls)
        if attempts:
            continue
        units.setdefault(_unit_key(tid, prov), []).append(cls)
    out = {c: 0 for c in CLASSES}
    for classes in units.values():
        out[min(classes, key=_PRECEDENCE.index)] += 1
    out.update(units=len(units), copies=sum(len(v) for v in units.values()),
               observed=bool(units))
    return out


def d1_enabled() -> bool:
    """Decision D1 (user, 2026-10-08): a rule needs at least one verified positive own unit to be trialled.
    On by default; TEHM_D1_REQUIRE_VERIFIED_POSITIVE=0 restores the previous rule, so D1's effect can be
    evaluated on its own (it is a behaviour change, not a counting fix)."""
    import os
    return os.environ.get("TEHM_D1_REQUIRE_VERIFIED_POSITIVE", "1") != "0"


def positive_rule_substitutions(conn: sqlite3.Connection, rule_id: str) -> list[dict]:
    """One parameter vote per canonical, confirmed-positive own physical attempt.

    Same evidence classes/unit keys as admission. Conflicting copies or
    substitutions are not positive parameter authority, even if another copy
    has a PASS label. History compatibility never turns unknown rollback into
    a verified parameter witness.
    """
    own={}
    for (raw,) in conn.execute('SELECT source_substitution_json FROM tehm_rule_sources WHERE rule_id=?',(rule_id,)):
        for tid,subs in _j(raw).items():own.setdefault(tid,[]).append(subs or {})
    attempts_table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tehm_attempts'").fetchone()
    units={}
    for tid,substitutions in sorted(own.items()):
        row=conn.execute('SELECT outcome,verifier_json,observation_delta_json,provenance_json '
                         'FROM tehm_transitions WHERE transition_id=?',(tid,)).fetchone()
        if row is None:continue
        cls=classify(row[0],_j(row[1]),_j(row[2]))
        keys=['rec:'+r[0] for r in conn.execute('SELECT record_id FROM tehm_attempts WHERE transition_id=?',(tid,))] if attempts_table else []
        for key in keys or [_unit_key(tid,_j(row[3]))]:
            units.setdefault(key,[]).extend((cls,subs) for subs in substitutions)
    out=[]
    for _,observations in sorted(units.items()):
        if (all(cls=='positive' for cls,_ in observations) and
                all(subs==observations[0][1] for _,subs in observations)):
            out.append(observations[0][1])
    return out


def caliber() -> str:
    import os
    c = os.environ.get("TEHM_EVIDENCE_CALIBER", "main")
    if c not in CALIBERS:
        raise ValueError(f"TEHM_EVIDENCE_CALIBER={c!r}: expected one of {CALIBERS}")
    return c


def positives(evidence: dict, caliber_name: str | None = None) -> int:
    c = caliber_name or caliber()
    return evidence["positive"] + (evidence.get("rollback_unknown", 0) if c == "history" else 0)


def admissible(evidence: dict, lifecycle_status: str | None, *, require_verified_positive: bool | None = None,
               caliber_name: str | None = None) -> bool:
    """Trial admission, the SAME rule for runtime and rebuild: a promoted rule stays admissible; otherwise
    candidate iff its canonical own positives (per caliber) outnumber its harmful units, and (D1) it has at
    least one verified positive own unit. A rule left out stays in shadow, kept for future verification."""
    if require_verified_positive is None:
        require_verified_positive = d1_enabled()
    if lifecycle_status == "promoted":
        return True
    pos = positives(evidence, caliber_name)
    if require_verified_positive:
        return pos >= 1 and pos > evidence["harmful"]
    return not evidence["observed"] or pos > evidence["harmful"]
