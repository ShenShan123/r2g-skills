"""Read-only P15 coverage diagnostics; never emit paired calibration labels.

Input: a frozen SQLite source and a JSON request with campaign_id and cases.
Each case must carry case_id, split=calibration, learner_eligible=false, query.
This request is diagnostic input, not independent execution/split authority.
Use bind_no_skill_source_coverage_case before actual paired execution to obtain
execution-bound evidence; this audit cannot retrofit historical case digests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from contracts import MemoryQuery
from tehm import db
from tehm.evaluation.no_skill_source_coverage import (
    derive_no_skill_source_coverage, verify_no_skill_source_coverage,
)
from tehm.ids import stable_dumps

MEMORY_ROOT = Path(__file__).resolve().parents[1]


def _sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def audit(source, request, *, output):
    source, request, output = (Path(p).resolve() for p in (source, request, output))
    if output.exists() or output.is_relative_to(MEMORY_ROOT) or output in {source, request}:
        raise ValueError("coverage output must be new and outside memory/source inputs")
    before = {"source": _sha(source), "request": _sha(request)}
    code = Path(derive_no_skill_source_coverage.__code__.co_filename).resolve()
    code_pins = {"auditor": {"path": str(Path(__file__).resolve()), "sha256": _sha(Path(__file__))},
                 "coverage_implementation": {"path": str(code), "sha256": _sha(code)}}
    payload = json.loads(request.read_text())
    campaign = payload.get("campaign_id")
    cases = payload.get("cases")
    if type(campaign) is not str or not campaign.strip() or not isinstance(cases, list) or not cases:
        raise ValueError("coverage request requires campaign_id and nonempty cases")
    if any(not isinstance(c, dict) or type(c.get("case_id")) is not str or not c["case_id"].strip()
           or c.get("split") != "calibration" or c.get("learner_eligible") is not False for c in cases):
        raise ValueError("coverage request requires explicit non-learner calibration cases")
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("coverage request contains duplicate case identities")
    conn = db.connect_read_only(source)
    rows = {}
    try:
        for case in cases:
            query = MemoryQuery(**case["query"])
            receipt = derive_no_skill_source_coverage(conn, campaign_id=campaign,
                case_id=case["case_id"], query=query)
            replay = verify_no_skill_source_coverage(conn, receipt, query=query)
            if not replay["verified"]:
                raise ValueError("coverage failed independent source replay")
            rows[case["case_id"]] = {"receipt": {**receipt.to_dict(),
                "receipt_id": receipt.receipt_id, "receipt_digest": receipt.receipt_digest},
                "replay": replay}
    finally:
        conn.close()
    if before != {"source": _sha(source), "request": _sha(request)}:
        raise ValueError("source/request changed during read-only audit")
    if any(_sha(Path(pin["path"])) != pin["sha256"] for pin in code_pins.values()):
        raise ValueError("coverage/auditor code changed during audit")
    report = {"version": "no-skill-source-coverage-audit-v1", "scope": "COVERAGE_ONLY_NOT_PAIRED_LABEL",
        "inputs": {"source": {"path": str(source), "sha256": before["source"]},
                   "request": {"path": str(request), "sha256": before["request"]},
                   **code_pins},
        "cases": rows, "source_bytes_unchanged": True,
        "request_membership_independently_verified": False, "paired_execution_bound": False,
        "calibration_labels_emitted": 0, "new_independent_calibration_samples": 0,
        "router_prediction_used": False, "provider_calls": 0,
        "canonical_memory_mutation": "none", "training_support_update": "none",
        "production_runtime_imported": False, "promotion_attempted": False}
    report["report_digest"] = "sha256:" + hashlib.sha256(stable_dumps(report).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.source, args.request, output=args.output)
    print(json.dumps({"report_digest": report["report_digest"], "cases": len(report["cases"]),
                      "scope": report["scope"], "calibration_labels_emitted": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
