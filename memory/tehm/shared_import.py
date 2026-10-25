"""Source-bound shared-label import. Legacy rows keep their original adapter.

An authority file is produced only after independent ann-v2 verification and
exact reconstruction of the frozen shared package. A label alone is not proof.
Raw counts are preserved; binary typed-check outcomes are a separate measure.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=64)
def _bound_bytes(path, expected, stamp):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("shared authority input hash changed: " + path)
    return data


def bound_bytes(path, expected):
    p = Path(path)
    st = p.stat()
    return _bound_bytes(str(p), expected, (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))


def bound_json(path, expected):
    return json.loads(bound_bytes(path, expected))


def verified_label(unit):
    proof = unit.get("shared_evidence_proof") or {}
    if not proof.get("authority") or not proof.get("authority_sha256"):
        raise ValueError("shared label requires verified import authority proof")
    auth = bound_json(proof["authority"], proof["authority_sha256"])
    if auth.get("schema") != "tehm-shared-import-authority-v1":
        raise ValueError("unknown shared import authority")
    # Stat-keyed caching still notices changed evidence after an earlier import.
    for path, expected in auth.get("files", {}).items():
        bound_bytes(path, expected)
    if not auth.get("verifier_receipts"):
        raise ValueError("shared authority has no independent verification receipt")
    for path in auth["verifier_receipts"]:
        if path not in auth.get("files", {}):
            raise ValueError("unbound independent verification receipt")
        report = bound_json(path, auth["files"][path])
        if report.get("gate") != "PASS" or report.get("critical") != 0:
            raise ValueError("shared independent verification did not pass")
    original = {k: v for k, v in unit.items() if k != "shared_evidence_proof"}
    if auth.get("units", {}).get(unit.get("unit_id")) != digest(original):
        raise ValueError("shared unit or label changed after verification")
    lab = unit["shared_label"]
    if type(lab.get("admitted")) is not bool:
        raise ValueError("shared admission must be an explicit boolean")
    kind = lab.get("outcome_class")
    if kind not in (None, "admitted_positive", "harm", "cleared_new_failure", "improved",
                    "no_change", "cleared_unconfirmed", "tool_no_op"):
        raise ValueError("unknown shared outcome class")
    rb = lab.get("rolled_back") or {}
    if rb.get("status") == "KNOWN" and type(rb.get("value")) is not bool:
        raise ValueError("known shared rollback must be boolean")
    binding = lab.get("binding") or {}
    bound = binding.get("pre") == binding.get("post") == "KNOWN" and bool(lab.get("signature"))
    if lab["admitted"]:
        if (kind != "admitted_positive" or not bound or rb.get("status") != "KNOWN"
                or rb.get("value") is not False
                or set((lab.get("step_effect") or {}).values()) != {"CLEARED"}
                or any(v == "KNOWN-present" for v in (lab.get("new_failures") or {}).values())):
            raise ValueError("contradictory shared positive evidence")
    elif kind == "admitted_positive":
        raise ValueError("non-admitted unit cannot be an admitted positive")
    return lab, proof, bound


def apply_shared_label(record, unit):
    lab, proof, bound = verified_label(unit)
    kind, rb = lab.get("outcome_class"), lab.get("rolled_back") or {}
    marker = ("true" if rb.get("value") is True else "false") if rb.get("status") == "KNOWN" else "UNKNOWN"
    measured = bound and kind in {"admitted_positive", "harm", "no_change", "improved",
                                 "cleared_new_failure", "cleared_unconfirmed"}
    verdict = "FAIL" if measured and kind in {"harm", "no_change"} else "PASS" if measured else "UNKNOWN"
    removed = measured and kind in {"admitted_positive", "cleared_new_failure", "cleared_unconfirmed"}
    cls = ("positive" if lab["admitted"] else "harmful" if measured and kind == "harm" else
           "no_clear" if measured and kind == "no_change" else
           "rollback_unknown" if measured and kind == "cleared_unconfirmed" and marker == "UNKNOWN" else
           "rolled_back" if marker == "true" and kind != "harm" else "no_evidence")
    original = record.observation_delta
    record.observation_delta = {**original,
        "original_failure": "REMOVED" if removed else "PRESENT" if measured else "UNKNOWN",
        # Not an overflow count: a separately identified typed target-check status.
        "failing_tests": {"before": 1, "after": 0 if removed else 1} if measured else None,
        "experiment_kind": "REPAIR" if measured else "OBSERVATION",
        "created_regressions": ["ann-v2:target_regressed"] if measured and kind == "harm" else [],
        "newly_observed_failures": ["ann-v2:cleared_new_failure"] if measured and kind == "cleared_new_failure" else [],
        "rolled_back": marker, "rollback_reason": original.get("rollback_reason")}
    evidence = {"schema": "tehm-shared-label-evidence-v1", "unit_id": unit["unit_id"],
        "label_sha256": digest(lab), "authority": proof["authority"],
        "authority_sha256": proof["authority_sha256"], "admitted": lab["admitted"],
        "outcome_class": kind, "evidence_class": cls, "label": lab,
        "measurement": "ann-v2 bound typed-target-check status; raw counts unchanged"}
    record.verification = {**record.verification, "verdict": verdict, "oracle_complete": bool(measured),
        "extractor_version": "shared-ann-v2-import-v1", "input_binding": {"shared_history": evidence},
        "evidence_refs": ["shared-unit:" + unit["unit_id"], "authority-sha256:" + proof["authority_sha256"]]}
    # Identity must bind the semantics too, not only optional verifier provenance.
    record.action["payload"]["shared_history_label_sha256"] = digest(lab)
    record.provenance = {**(record.provenance or {}), "shared_history": evidence}
    return record
