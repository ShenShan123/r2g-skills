#!/usr/bin/env python3
"""Check that frozen ORFS sources survive an isolated project copy.

Config/source existence alone is insufficient: run_orfs.sh also requires the
conventional local constraints/constraint.sdc. External design references are
valid Make inputs but are not a self-contained campaign project. This audit
does not execute EDA, mutate sources, import support, or establish signoff.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_r3_orfs_interference_source_bound_inputs import (
    _config_source_map, _digest, _read, _sha256, _write,
)


class OrfsProjectLayoutError(ValueError):
    """A frozen project cannot be reproduced by the isolated flow wrapper."""


def audit_project_layout(preregistration, *, output):
    prereg_path = Path(preregistration).resolve()
    output_path = Path(output).resolve()
    if output_path.exists():
        raise OrfsProjectLayoutError("layout audit output must be new")
    prereg = _read(prereg_path, "preregistration")
    if (prereg.get("evaluation_only") is not True or
            prereg.get("canonical_memory_mutation") != "none" or
            prereg.get("production_runtime_imported") is not False):
        raise OrfsProjectLayoutError("layout audit requires an evaluation-only campaign")
    cases = prereg.get("cases")
    if not isinstance(cases, list) or not cases:
        raise OrfsProjectLayoutError("layout audit requires declared cases")
    rows, seen = {}, set()
    for case in cases:
        cid = case["case_id"]
        if cid in seen:
            raise OrfsProjectLayoutError("duplicate layout case")
        seen.add(cid)
        project = Path(case["project_dir"]).resolve()
        sources = _config_source_map(project)
        conventional_sdc = project / "constraints" / "constraint.sdc"
        if not conventional_sdc.is_file():
            raise OrfsProjectLayoutError("isolated ORFS project lacks local constraints/constraint.sdc")
        if sources["SDC_FILE"] != (conventional_sdc.resolve(),):
            raise OrfsProjectLayoutError("SDC_FILE differs from the conventional isolated-flow SDC")
        for key, paths in sources.items():
            expected_root = project / ("rtl" if key == "VERILOG_FILES" else "constraints")
            if any(not path.is_relative_to(expected_root) for path in paths):
                raise OrfsProjectLayoutError("external or escaping design source cannot survive isolated project copy")
        actual = {str(path): _sha256(path).removeprefix("sha256:")
                  for paths in sources.values() for path in paths}
        declared = {}
        for pin in case["source_inputs"]:
            path = str(Path(pin["path"]).resolve())
            if path in declared:
                raise OrfsProjectLayoutError("duplicate source input pin")
            declared[path] = pin["sha256"].removeprefix("sha256:")
        if actual != declared:
            raise OrfsProjectLayoutError("declared source pins differ from self-contained config sources")
        rows[cid] = {"project_dir": str(project), "source_inputs": actual,
                     "config": {"path": str(project / "constraints" / "config.mk"),
                                "sha256": _sha256(project / "constraints" / "config.mk")},
                     "conventional_sdc_verified": True, "external_design_references": []}
    report = {"version": "r3-orfs-self-contained-project-layout-audit-v1",
              "campaign_id": prereg["campaign_id"], "passed": True,
              "preregistration": {"path": str(prereg_path), "sha256": _sha256(prereg_path)},
              "cases": rows, "audit_source_binding": {
                  "path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
              "eda_executed": False, "signoff_or_equivalence_claimed": False,
              "canonical_memory_mutation": "none", "production_runtime_imported": False,
              "evaluation_only": True, "learner_support_imported": False}
    report["report_digest"] = _digest(report)
    _write(output_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_project_layout(args.preregistration, output=args.output)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"passed": report["passed"], "report_digest": report["report_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
