"""Source-backed status only; incomplete integration is not a Table 4 score."""
import json
import subprocess
import time

from bridge import ROOT, stable
from phase_gate import inspect


def collect():
    episodes = []
    for path in sorted((ROOT / "hosts").glob("*/*/episodes/*/episode.json")):
        raw = json.loads(path.read_text())
        episodes.append({"host": raw["host"], "arm": raw["arm"], "task": raw["task"],
                         "status": raw["status"], "cost": raw["cost"],
                         "receipt": str(path.relative_to(ROOT))})
    for path in sorted((ROOT / "hosts").glob("*/*/episodes/*/incident-recovery.json")):
        if (path.parent / "episode.json").exists():
            continue
        raw = json.loads(path.read_text())
        episodes.append({"host": raw["host"], "arm": raw["arm"], "task": raw["task"],
                         "status": raw["status"], "cost": raw["cost"],
                         "receipt": str(path.relative_to(ROOT)), "derived_not_original": True})
    sources = {name: subprocess.check_output(["git", "-C", str(ROOT / "sources" / name),
                "rev-parse", "HEAD"], text=True).strip()
               for name in ["rtlfixer", "rtlscout", "mini-swe-agent", "verilog-eval", "SWE-bench"]}
    states = [ROOT / "reports" / ("continuation-state-" + rev + ".json") for rev in ('c3', 'c2', 'c1')]
    continuation = next((p for p in states if p.exists()), states[-1])
    state = json.loads(continuation.read_text()) if continuation.exists() else {}
    live = int(bool(state.get("running")))
    return {"schema": "table4-integration-status-v1", "updated_unix": time.time(),
        "paper_data": False, "status": state.get("status", "CONTINUATION_AUTHORIZED_AWAITING_HOST_CHECKS"),
        "paid_launch_authorized": True, "registered_scope": 12,
        "episodes_started": len(episodes)+live, "episodes_ended": len(episodes),
        "episodes_not_started": 12-len(episodes)-live, "continuation": state,
        "episodes": episodes, "provider_accounting": inspect(), "source_revisions": sources,
        "native_source_changes": {"verilog-eval": "two-line execution enablement per upstream sandbox instructions",
                                  "rtlfixer": "none", "rtlscout": "none", "mini-swe-agent": "none"},
        "tests": {"modern_junit": "reports/modern-tests-final.xml",
                  "rtlfixer_junit": "reports/rtlfixer-tests-final.xml"},
        "limitations": [
            "Native source-reuse driver branches tested with explicit unit fixture choices; naturally selected direct reuse is not yet demonstrated",
            "SWE-bench official evaluator/runtime and instance environments unavailable; software shell success is UNKNOWN",
            "RTLFixer subset is native VerilogEval integration, not yet RTLRepair",
            "Cold-start empty private stores; first-batch integration is not a learned-transfer result",
            "Complete child-process CPU and monetary pricing are not measured; never treat as zero",
            "Unresolved request usages remain UNKNOWN, so total actual token cost cannot be compared",
        ],
        "resumption": "Exact acknowledged UNKNOWNs only; C3 is restricted to the last never-started RTLScout TEHM fifo task; any new UNKNOWN still pauses; software withheld"}


if __name__ == "__main__":
    result = collect()
    (ROOT / "reports/integration-status.json").write_text(stable(result) + "\n")
    print(stable({"status": result["status"], "started": result["episodes_started"],
                  "not_started": result["episodes_not_started"], "accounting": result["provider_accounting"]}))
