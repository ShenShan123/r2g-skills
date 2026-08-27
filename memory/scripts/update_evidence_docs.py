#!/usr/bin/env python3
"""Render the current Evidence Contract v3 facts into both design documents."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402

START = "<!-- TEHM_EVIDENCE_V3_START -->"
END = "<!-- TEHM_EVIDENCE_V3_END -->"


def _json(root: Path, rel: str) -> dict:
    return json.loads((root / rel).read_text())


def render(bundle: Path) -> str:
    manifest = _json(bundle, "bundle_manifest.json")
    metadata = manifest["metadata"]
    source = metadata["source_state"]
    counts = _json(bundle, "evaluation/source_freeze_v2_manifest.json")["counts"]
    calibration = _json(bundle, "evidence/physical/calibration_report.json")
    readiness = _json(bundle, "evidence/physical/parametric_readiness.json")
    audit = _json(bundle, "evidence/audit/honesty_report.json")
    tests = _json(bundle, "evidence/tests/pytest_memory_tests.json")
    m0m8 = _json(bundle, "evaluation/m0_m8_v2_report.json")["summary"]
    statuses = {key: value["status"] for key, value in calibration["policies"].items()}
    status_text = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    green = all(item.get("ok") for item in audit.values())
    return "\n".join([
        START,
        "### Evidence Contract v3（由 freeze manifest 自动生成）",
        "",
        f"- Freeze：`{metadata['evidence_contract']}`；完整 bundle digest 与文件哈希见"
        " `bundle_manifest.json`。",
        f"- TEHM snapshot：{counts['tehm_transitions']} transitions / {counts['tehm_views']} views / "
        f"{counts['tehm_physical_effects']} physical effects / {counts['tehm_rules']} rules。",
        f"- 回归：`{tests['passed']} passed`；H1–H12 + A1 审计：`{'ALL GREEN' if green else 'FAIL'}`；"
        f"H7=`{audit['H7']['detail']}`；H10=`{audit['H10']['detail']}`。",
        f"- H11：export → import → export byte-stable；reproduce 入口为 `reproduce.sh`。",
        f"- M0/M1/M8 pilot：M0={m0m8['M0']['successes']}/{m0m8['M0']['tasks']}，"
        f"M1={m0m8['M1']['successes']}/{m0m8['M1']['tasks']}，"
        f"M8={m0m8['M8']['successes']}/{m0m8['M8']['tasks']}；该结果仍不是普适 benchmark。",
        f"- Physical calibration：memory count {calibration['physical_memory_count_before']} → "
        f"{calibration['physical_memory_count_after']}；策略状态：`{status_text}`。",
        f"- Parametric readiness：`{readiness['status']}`；Parametric View："
        f"`{readiness['parametric_view_status']}`；lineage diversity "
        f"{readiness['criteria']['observed_independent_heldout_lineages']}/"
        f"{readiness['criteria']['minimum_independent_heldout_lineages']}。",
        "- Source binding（HEAD、dirty-diff、workspace state digest）记录在"
        " `bundle_manifest.json`，由 reproduce 验证。",
        "",
        "Parametric View 只有在 distance、coverage、uncertainty、lineage diversity 四项同时通过，"
        "并且该 bundle 可重放后，才允许进入 shadow RFC。",
        END,
    ]) + "\n"


def update(path: Path, block: str) -> None:
    text = path.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if pattern.search(text):
        text = pattern.sub(block.rstrip("\n"), text, count=1)
    else:
        text = text.rstrip() + "\n\n" + block
    path.write_text(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path,
                    default=resolve_bundle(require_exists=False))
    ap.add_argument("--readme", type=Path, default=Path("memory/README.md"))
    ap.add_argument("--design", type=Path,
                    default=Path("memory/docs/Typed_Executable_Hardware_Memory_R2G.md"))
    args = ap.parse_args(argv)
    block = render(args.bundle.resolve())
    update(args.readme.resolve(), block)
    update(args.design.resolve(), block)
    print(block, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
