#!/usr/bin/env python3
"""Run compatibility negative cases and profile-bound Yosys checks."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.rtl.compatibility import structural_compatibility  # noqa: E402
from tehm.rtl.equivalence import verify_profile_equivalence  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "rtl_projects"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    cases = {item["id"]: item for item in manifest.get("cases", [])}
    profile_reports = []
    for profile in manifest.get("profile_matrix", []):
        name = str(profile["profile"])
        mismatch = next((x for x in manifest.get("profile_matrix", [])
                         if x["profile"] != name), None)
        source = {"compatibility_profile": name, "module": "m", "case_expr": "state"}
        candidate = {"compatibility_profile": (mismatch or {}).get("profile"),
                     "module": "m", "case_expr": "state"}
        negative = {
            "profile_mismatch": structural_compatibility(source, candidate),
            "missing_profile": structural_compatibility(
                {"module": "m", "case_expr": "state"},
                {"module": "m", "case_expr": "state"}),
            "module_context_mismatch": structural_compatibility(
                {"compatibility_profile": name, "module": "m_a", "case_expr": "state"},
                {"compatibility_profile": name, "module": "m_b", "case_expr": "state"}),
            "case_context_mismatch": structural_compatibility(
                {"compatibility_profile": name, "module": "m", "case_expr": "state_a"},
                {"compatibility_profile": name, "module": "m", "case_expr": "state_b"}),
        }
        pair = profile.get("formal_fixture_pair")
        formal = {"verdict": "UNKNOWN", "reason": "no_frozen_fixture_pair"}
        if isinstance(pair, list) and len(pair) == 2:
            formal = _run_formal_pair(pair[0], pair[1], name)
        profile_reports.append({
            "profile": name, "domain": profile.get("domain"),
            "negative": negative,
            "negative_expected": {key: cases[key]["expected"]
                                   for key in profile.get("negative_cases", [])
                                   if key in cases},
            "formal": formal,
            "formal_promotion_eligible": formal.get("verdict") == "PASS",
            "formal_negative_rejected": formal.get("verdict") in {"FAIL", "UNKNOWN"},
        })
    selftest = _run_formal_pair("p4_ast_literal_a", "p4_ast_literal_c",
                                "rtl.ast.literal_rewrite.v1")
    report = {
        "version": "rtl-profile-oracle-v1",
        "manifest": str(args.manifest.resolve()),
        "profiles": profile_reports,
        "all_negative_expectations_met": all(
            _negative_ok(row["negative"], row["negative_expected"])
            for row in profile_reports),
        # The matrix pairs are intentionally non-equivalent bug/fix witnesses:
        # rejecting them is the expected result.  A separate equivalent-pair
        # self-test proves that the oracle can also return PASS.
        "all_formal_negative_cases_rejected": all(
            row["formal_negative_rejected"] for row in profile_reports),
        "oracle_selftest": selftest,
        "oracle_selftest_pass": selftest.get("verdict") == "PASS",
        "all_formal_profiles_proven": False,
        "unknown_or_fail_is_not_promotion_evidence": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"profiles": len(profile_reports),
                      "negative_ok": report["all_negative_expectations_met"],
                      "formal_negative_rejected": report["all_formal_negative_cases_rejected"],
                      "oracle_selftest_pass": report["oracle_selftest_pass"]},
                     sort_keys=True))
    return 0


def _run_formal_pair(reference_dir: str, candidate_dir: str, profile: str) -> dict:
    ref = FIXTURES / reference_dir / "rtl"
    cand = FIXTURES / candidate_dir / "rtl"
    ref_files, cand_files = sorted(ref.glob("*.v")), sorted(cand.glob("*.v"))
    if len(ref_files) != 1 or len(cand_files) != 1:
        return {"verdict": "UNKNOWN", "reason": "fixture_rtl_file_count"}
    ref_top = _top(ref_files[0])
    cand_top = _top(cand_files[0])
    ref_manifest = json.loads((FIXTURES / reference_dir / "manifest.json").read_text())
    cand_manifest = json.loads((FIXTURES / candidate_dir / "manifest.json").read_text())
    ref_action = dict(ref_manifest.get("fix") or {})
    cand_action = dict(cand_manifest.get("fix") or {})
    # Preserve the profile matrix's contract even when a historical fixture's
    # action used a domain-specific profile alias.
    ref_action["compatibility_profile"] = profile
    cand_action["compatibility_profile"] = profile
    return verify_profile_equivalence(
        reference_files=ref_files, candidate_files=cand_files,
        reference_top=ref_top, candidate_top=cand_top,
        reference_action=ref_action, candidate_action=cand_action)


def _top(path: Path) -> str:
    match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", path.read_text())
    if not match:
        raise ValueError(f"module top missing: {path}")
    return match.group(1)


def _negative_ok(actual, expected):
    return all(actual.get(key, {}).get("status") == value
               for key, value in expected.items())


if __name__ == "__main__":
    raise SystemExit(main())
