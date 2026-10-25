"""Bound synthetic raw LVS evidence for gate tests; never calls an EDA tool."""
import hashlib
import json
from pathlib import Path


def add_lvs_evidence(project, run, *, record_summary=True, create_gds=True):
    project, run = Path(project), Path(run)
    root = project / "lvs"
    root.mkdir(exist_ok=True)
    (project / "metadata.json").write_text(json.dumps({"top": "TOP"}))
    nets = " ".join(f"net({i} name({n}))" for i, n in enumerate(("A", "Z", "VDD", "VSS"), 1))
    pins = " ".join(f"pin({i} name({n}))" for i, n in enumerate(("A", "Z", "VDD", "VSS"), 1))
    device = "device(1 NMOS_VTL param(W 1) param(L .05) terminal(S 4) terminal(G 1) terminal(D 2) terminal(B 4))"
    circuit = f"circuit(TOP {nets} {pins} {device})"
    pairs = " ".join(f"net({i} {i} match)" for i in range(1, 5))
    pairs += " " + " ".join(f"pin({i} {i} match)" for i in range(4))
    (root / "6_lvs.lvsdb").write_text(
        f"#%lvsdb-klayout\nlayout(top(TOP) class(NMOS_VTL MOS4) {circuit})\n"
        f"reference(class(NMOS_VTL MOS4) {circuit})\n"
        f"xref(circuit(TOP TOP match xref({pairs} device(1 1 match))))\n")
    (root / "6_lvs.log").write_text("CONGRATULATIONS! Netlists match.\n")
    (root / "lvs_run.log").write_text("synthetic checker fixture\n")
    (root / "6_final.cdl").write_text(".SUBCKT TOP A Z VDD VSS\n.ENDS TOP\n")
    (root / "audit_masters.cdl").write_text("* synthetic test masters\n")
    deck = Path(__file__).parents[2] / "signoff-loop/assets/platforms/nangate45/lvs/FreePDK45.lylvs"
    (root / "audit_rule.lylvs").write_bytes(deck.read_bytes())
    gds = next((run / sub / "6_final.gds" for sub in ("results", "final") if (run / sub / "6_final.gds").exists()), None)
    if gds is None and create_gds:
        gds = run / "final/6_final.gds"
        gds.parent.mkdir(parents=True, exist_ok=True)
        gds.write_bytes(b"synthetic fixture GDS")
    digest = hashlib.sha256(gds.read_bytes()).hexdigest() if gds else "0" * 64
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir() if path.is_file()}
    (root / "lvs_execution.json").write_text(json.dumps({
        "schema_version": 1, "engine": "klayout", "exit_code": 0, "run_tag": run.name,
        "gds_sha256": digest, "rule_sha256": files["audit_rule.lylvs"],
        "masters_sha256": files["audit_masters.cdl"], "tool_sha256": "a" * 64, "files": files}))
    if record_summary:
        report = project / "reports/lvs.json"
        doc = json.loads(report.read_text())
        doc["gds_sha256"] = digest
        report.write_text(json.dumps(doc))
