#!/usr/bin/env python3
"""Verified shared-label import and a two-episode TEHM-only DEV correction.

No history, NB reference, running package or old episode is overwritten. prepare
is offline; run is a separate paid/EDA launch under the user's 2026-10-19 request.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[1]
sys.path[:0] = [str(REPO / "memory"), str(SCRIPTS)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=1) + "\n")


def lines(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def validated_dispatcher_source(base, candidate):
    """Allow only the already-tested dispatcher addition, not changed repair code."""
    restored = candidate.replace('import fcntl\n', '', 1).replace('import subprocess\n', '', 1)
    start = restored.index('# ---------------------------------------------------------------------------------- dispatcher\n')
    end = restored.index('def main(argv=None)', start)
    restored = restored[:start] + restored[end:]
    restored = restored.replace(
        '        st = dispatch(a.root.resolve())\n'
        '        print(json.dumps({k: st.get(k) for k in ("status", "reason", "next_wave", "flows_charged")}))\n',
        '        root = a.root.resolve()\n        B.batch(root, "DEV")\n', 1)
    if restored != base:
        raise ValueError("unrelated dispatcher delta must not enter correction")
    return candidate


def prepare_export(package, out, prior_receipts=None):
    import t2_shared_history as SH
    from ann_v2 import verify as V
    from tehm.shared_import import digest

    package, out = Path(package).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError("no overwrite of a previous verified import")
    man = load(package / "manifest.json")
    if sha(package / "shared_units.jsonl") != man["units_sha256"]:
        raise ValueError("shared units hash changed")
    inputs = man["inputs"]
    for path, expected in inputs.items():
        if sha(path) != expected:
            raise ValueError("shared package input changed: " + path)
    sources = [Path(p) for p in inputs if Path(p).name == "source_export.jsonl"]
    annotations = [Path(p) for p in inputs if Path(p).name == "ann_v2_dev.jsonl"]
    source_ids = {p: {u["unit_id"] for u in lines(p)} for p in sources}
    out.mkdir(parents=True)
    reports = []
    for i, ann in enumerate(annotations):
        ids = {r["unit_id"] for r in lines(ann)}
        matches = [p for p, sids in source_ids.items() if ids == sids]
        if len(matches) != 1:
            raise ValueError("annotation SOURCE ownership is ambiguous: " + str(ann))
        src = matches[0]
        if prior_receipts is None:
            result = V.verify(ann, src.parent, expect_source_sha=inputs[str(src)])
        else:
            result = load(Path(prior_receipts) / f"independent-verifier-{i}.json")
            ri = result.get("inputs", {})
            if (ri.get("annotation") != str(ann) or ri.get("annotation_sha256") != sha(ann)
                    or ri.get("source_export_sha256") != sha(src)
                    or result.get("records") != len(ids)):
                raise ValueError("prior verifier receipt is not bound to these inputs")
        receipt = out / f"independent-verifier-{i}.json"
        save(receipt, result)
        reports.append(receipt)
        if result["gate"] != "PASS" or result["critical"]:
            raise ValueError("independent shared evidence verification failed: " + str(receipt))
        print(json.dumps({"gate": result["gate"], "records": result["records"], "source": str(src)}), flush=True)
    if not reports:
        raise ValueError("no independent annotation verification")
    ov_paths = [Path(p) for p in inputs if Path(p).name == "overrides.json"]
    if len(ov_paths) > 1:
        raise ValueError("several override sources")
    # The frozen v4 package predates unset-knob/default normalization. Reproduce
    # its literal edit labels, not today's changed text view. No runtime policy
    # is changed, and exact equality of every field is still required.
    defaults = SH.EI.ORFS_DEFAULTS
    try:
        SH.EI.ORFS_DEFAULTS = {}
        reconstructed, _ = SH.build(sources, annotations, man["split"], man["view"],
                                    load(ov_paths[0]) if ov_paths else None)
    finally:
        SH.EI.ORFS_DEFAULTS = defaults
    original = lines(package / "shared_units.jsonl")
    if {u["unit_id"]: u for u in reconstructed} != {u["unit_id"]: u for u in original}:
        raise ValueError("shared labels do not reconstruct from verified original inputs")
    units = SH.tehm_view(original)
    files = {**inputs, str(package / "manifest.json"): sha(package / "manifest.json"),
             str(package / "shared_units.jsonl"): man["units_sha256"]}
    files.update({str(p): sha(p) for p in reports})
    authority = out / "authority.json"
    save(authority, {"schema": "tehm-shared-import-authority-v1", "shared_package": str(package),
         "shared_units_sha256": man["units_sha256"], "files": files,
         "verifier_receipts": [str(p) for p in reports],
         "units": {u["unit_id"]: digest(u) for u in units}})
    proof = {"authority": str(authority), "authority_sha256": sha(authority)}
    for u in units:
        u["shared_evidence_proof"] = proof
    export = out / "source_export.jsonl"
    export.write_text("".join(json.dumps(u, sort_keys=True) + "\n" for u in units))
    save(out / "source_manifest.json", {"export_sha256": sha(export), "view": man["view"],
         "shared_units_sha256": man["units_sha256"], "authority": proof,
         "note": "same shared history; corrected TEHM evidence mapping only"})
    return out


def build_store(export, out, code):
    from tehm import neutral_export as NX, evidence
    from tehm_backend import TehmMemoryBackend

    export, out = Path(export), Path(out)
    if out.exists():
        raise ValueError("no overwrite of an earlier TEHM store")
    units = NX.read_export(export)
    backend = TehmMemoryBackend(db_path=out / "tehm.sqlite", artifact_root=out / "artifacts", read_only_eval=False)
    started, cpu_started = time.time(), time.process_time()
    conn, artifacts = backend._open()
    receipts = NX.import_units(conn, artifacts, units)
    parity = []
    by_record = {r.record_id: r for r in receipts}
    for u in units:
        record = NX.unit_record(u)
        receipt = by_record[record.record_id]
        row = conn.execute("SELECT outcome,verifier_json,observation_delta_json FROM tehm_transitions WHERE transition_id=?",
                           (receipt.transition_id,)).fetchone()
        cls = evidence.classify(row[0], json.loads(row[1]), json.loads(row[2]))
        if (cls == "positive") != u["shared_label"]["admitted"]:
            raise ValueError("import admission parity failed: " + u["unit_id"])
        parity.append({"unit_id": u["unit_id"], "transition_id": receipt.transition_id,
                       "shared_admitted": u["shared_label"]["admitted"], "evidence_class": cls,
                       "outcome_class": u["shared_label"]["outcome_class"]})
    result = backend.rebuild()
    if not result.ok:
        raise ValueError("TEHM rebuild failed")
    if NX.import_units(conn, artifacts, units):
        raise ValueError("reimport not idempotent")
    admitted = []
    for rid, scope, status in conn.execute("SELECT rule_id,target_scope,status FROM tehm_rule_status"):
        ev = evidence.rule_evidence(conn, rid)
        if status not in ("retired", "quarantined") and evidence.admissible(ev, status):
            admitted.append({"rule": rid, "scope": scope, "status": status, "evidence": ev})
    receipt = {"source_export_sha256": sha(export / "source_export.jsonl"),
        "shared_units_sha256": load(export / "source_manifest.json")["shared_units_sha256"],
        "caliber": evidence.caliber(), "d1": evidence.d1_enabled(), "captured": len(receipts),
        "rules": conn.execute("SELECT count(*) FROM tehm_rules").fetchone()[0],
        "admitted_trial_main": admitted, "parity_passed": True, "parity_units": len(parity),
        "positives": sum(r["evidence_class"] == "positive" for r in parity),
        "wall_s": time.time() - started, "cpu_s": time.process_time() - cpu_started,
        "model_requests": 0, "tokens": 0, "eda_flows": 0, "code": str(code)}
    backend.close()
    save(out / "import-parity.json", parity)
    save(out / "store_receipt.json", receipt)
    print(json.dumps(receipt), flush=True)
    return receipt


def prepare(base, root, export, store):
    """Copy the validated base, with only the registered TEHM import delta."""
    import t2a_batch as B
    import t2a_envelope as E

    base, root = Path(base).resolve(), Path(root).resolve()
    if root.exists():
        raise ValueError("no overwrite of an earlier DEV correction")
    old = B.preflight(base, "DEV")
    root.mkdir(parents=True)
    shutil.copytree(base / "code", root / "code")
    changed = []
    for rel in ("memory/tehm/neutral_export.py", "memory/tehm/evidence.py", "memory/tehm/shared_import.py"):
        src, dst = REPO / rel, root / "code" / rel
        src_text = src.read_text()
        if dst.exists():
            # The worktree must differ from the frozen base by THIS repair only.
            if rel.endswith("neutral_export.py"):
                restored = src_text.replace('    record = _iter_record(project=Path("/export")',
                                            '    return _iter_record(project=Path("/export")', 1)
                restored = restored.replace('    if "shared_label" in unit:\n        from tehm.shared_import import apply_shared_label\n        return apply_shared_label(record, unit)\n    return record\n', '', 1)
            else:
                begin = src_text.index('    shared = ((verifier.get("input_binding")')
                end = src_text.index('    verified = verifier.get("oracle_complete")', begin)
                restored = src_text[:begin] + src_text[end:]
            if restored != dst.read_text():
                raise ValueError("unrelated worktree delta must not enter correction: " + rel)
        shutil.copy2(src, dst)
        changed.append(rel)
    rel = "memory/scripts/t2_dev3.py"
    dst = root / "code" / rel
    validated_dispatcher_source(dst.read_text(), (REPO / rel).read_text())
    shutil.copy2(REPO / rel, dst)
    changed.append(rel)
    shutil.copy2(__file__, root / "code/memory/scripts" / Path(__file__).name)
    shutil.copytree(store, root / "store")
    shutil.copytree(base / "controls", root / "controls")
    shutil.copytree(base / "tehm-v2", root / "tehm-v2")
    pol = load(base / "tehm-v2-policy.json")
    pol["index"] = str(root / "tehm-v2/evidence_index.json")
    save(root / "tehm-v2-policy.json", pol)
    task = next(t for t in old["tasks"] if t["task"] == "chacha_core/u72")
    template = root / "tasks/chacha_core__u72"
    shutil.copytree(task["template"], template)
    cfg = template / "constraints/config.mk"
    cfg.write_text(cfg.read_text().replace(str(task["template"]) + "/", str(template) + "/"))
    if cfg.read_text().replace(str(template) + "/", str(task["template"]) + "/") != (Path(task["template"]) / "constraints/config.mk").read_text():
        raise ValueError("task configuration changed beyond path relocation")
    man = load(template / "manifest.json")
    for rel, expected in man["immutable"]["files"].items():
        if "sha256:" + sha(template / rel) != expected:
            raise ValueError("immutable baseline task input changed: " + rel)
    man.update(initial_config=E.effective_config(cfg.read_text()), initial_config_sha256=sha(cfg), source_project=str(template))
    save(template / "manifest.json", man)
    for name in ("episode-budget.json", "online-input-authorization.json", "inventory.json", "dev.json"):
        shutil.copy2(base / name, root / name)
    save(root / "phase-budget.json", B.formal_phase_budget())
    (root / "phase").mkdir()
    (root / "phase/resource_ledger.jsonl").write_bytes(b"")
    nb = [base / "episodes" / f"chacha_core__u72__NB__r{r}" / "episode.json" for r in (1, 2)]
    for p in nb:
        if load(p).get("accepted") is not True:
            raise ValueError("reused NB reference is not accepted")
    save(root / "authorization.json", {"user_text": "没事现在修复后重新跑一下TEHM的实验",
        "scope": "chacha_core/u72 TM0 r1/r2 only; at most 3 repair generations each; existing O1 input policy",
        "date": "2026-10-19 Asia/Shanghai", "no_new_history": True,
        "not_authorized": ["NB rerun", "RECIPE rerun", "learning-curve mutation", "formal Table 2", "external builds"]})
    save(root / "baseline-reuse.json", {"reference": str(base), "episodes": {str(p): sha(p) for p in nb},
        "boundary": "DEV correction with historical NB reference, not a fresh simultaneous paired trial",
        "delta": changed, "shared_history_unchanged": True})
    files = {str(p): sha(p) for sub in ("code", "store", "controls", "tasks", "tehm-v2")
             for p in (root / sub).rglob("*") if p.is_file()}
    files.update({str(root / n): sha(root / n) for n in ("episode-budget.json", "phase-budget.json",
        "online-input-authorization.json", "tehm-v2-policy.json", "authorization.json", "baseline-reuse.json")})
    files.update(old["external_hashes"])
    files.update({str(p): sha(p) for p in nb})
    authority = load(Path(export) / "authority.json")
    files.update(authority["files"])
    files[str(Path(export) / "authority.json")] = sha(Path(export) / "authority.json")
    reg = copy.deepcopy(old)
    receipt = load(Path(store) / "store_receipt.json")
    reg["history"].update(tehm_rules=receipt["rules"], tehm_admissible=len(receipt["admitted_trial_main"]),
                           corrected_import_sha256=receipt["source_export_sha256"])
    reg.update(schema="t2-tehm-import-correction-v1", registered_at=time.time(),
        files=files, runtime_hashes={str(Path(p).relative_to(root / "code")): h for p, h in files.items()
                                    if Path(p).is_relative_to(root / "code")},
        arms=["TM0"], tasks=[{**task, "template": str(template)}], repeats={"chacha_core/u72": 2},
        plan=[{"task": "chacha_core/u72", "arm": "TM0", "repeat": r} for r in (1, 2)],
        caps={"halt_on_unknown_usage": True},
        phase_binding={"budget_file": str(root / "phase-budget.json"), "budget_sha256": sha(root / "phase-budget.json"),
            "ledger_path": str(root / "phase/resource_ledger.jsonl"), "prior_ledger_bytes": 0,
            "prior_ledger_sha256": hashlib.sha256(b"").hexdigest()},
        scheduler={**old["scheduler"], "cpu_slots": [list(range(15, 23)) + list(range(111, 119))],
                   "concurrency": 1}, correction={"base": str(base), "changed_runtime_files": changed,
        "shared_units_sha256": load(Path(export) / "source_manifest.json")["shared_units_sha256"],
        "store_receipt": load(Path(store) / "store_receipt.json")})
    save(root / "registration.json", reg)
    # Exercise the private runtime's actual gate, not just the operator's imports.
    subprocess.run([sys.executable, str(root / "code/memory/scripts/t2a_batch.py"), "preflight",
                    "--root", str(root), "--split", "DEV"], check=True,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    subprocess.run([sys.executable, "-c",
                   "import t2_dev3; assert callable(t2_dev3.dispatch); "
                   "assert callable(t2_dev3.default_launcher); assert callable(t2_dev3.wave_gate)"],
                   check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                    "PYTHONPATH": str(root / "code/memory/scripts") + ":" + str(root / "code/memory")})
    return reg


def run(root):
    import t2_dev3 as D
    root = Path(root).resolve()
    if (root / "launch-receipt.json").exists():
        raise FileExistsError("refusing to restart a prior launch")
    if not callable(getattr(D, "dispatch", None)):
        raise RuntimeError("frozen dispatcher dependency missing; no launch")
    # An operator must prepare a separately labelled package after any failure.
    with (root / "launch-receipt.json").open("x") as stream:
        json.dump({"started": time.time(), "registration_sha256": sha(root / "registration.json"),
                   "scope": "two TM0 DEV episodes; no retry"}, stream)
    stop = threading.Event()
    def sample_load():
        with (root / "machine-load.jsonl").open("a") as stream:
            while not stop.is_set():
                stream.write(json.dumps({"ts": time.time(), "loadavg": os.getloadavg(),
                    "cpu_slots": load(root / "registration.json")["scheduler"]["cpu_slots"]}) + "\n")
                stream.flush()
                stop.wait(30)
    monitor = threading.Thread(target=sample_load, daemon=True)
    monitor.start()
    try:
        # Existing once-only worker, technical wave gate and no-retry dispatcher.
        return D.dispatch(root, flow_cap=float("inf"))
    finally:
        stop.set()
        monitor.join(timeout=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("export", "build", "prepare", "run"))
    ap.add_argument("--package", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--export", type=Path)
    ap.add_argument("--store", type=Path)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--root", type=Path)
    ap.add_argument("--prior-receipts", type=Path)
    a = ap.parse_args()
    if a.command == "export":
        prepare_export(a.package, a.out, a.prior_receipts)
    elif a.command == "build":
        build_store(a.export, a.out, REPO)
    elif a.command == "prepare":
        prepare(a.base, a.root, a.export, a.store)
    else:
        print(json.dumps(run(a.root)))


if __name__ == "__main__":
    main()
