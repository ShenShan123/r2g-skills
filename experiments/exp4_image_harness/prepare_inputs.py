#!/usr/bin/env python3
"""Prepare genuine placement/final pairs for the fixed graph/image cohort.

Run on 216. SKY130HD/GF180 retain their own placement ODB. Nangate45's
2026-10-18 harvest discarded it, so replay the original complete physical flow
in a separate workspace and keep both new stages. No conversion-arm costs are
charged for this shared input preparation, and no new signoff is claimed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def checked_run(command, log, name, timeout):
    with log.open("a") as output:
        try:
            subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["podman", "rm", "-f", name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise


def prepare_one(args, split, row, image):
    design = row["design"]
    source = Path(row["source_dir"])
    out = args.root / "data" / split / design
    record_file = out / "STAGED.json"
    if record_file.exists():
        record = json.loads(record_file.read_text())
        if all(sha(out / f) == v["sha256"] for f, v in record["files"].items()):
            return record
        raise RuntimeError(f"Previously prepared input changed: {design}")
    work = args.root / "work" / design
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    record = {"design": design, "platform": row["platform"], "split": split,
              "source_dir": str(source), "image_id": image,
              "started_at_utc": now(), "shared_input_preparation": True,
              "charged_to_harness": False}
    log = work / "preparation.log"
    try:
        for filename in ("config.mk", "constraint.sdc"):
            shutil.copy2(source / filename, out / filename)
        name = "exp4img-input-" + row["candidate_id"].lower() + "-" + row["platform"]
        if row["platform"] == "nangate45":
            for filename in ("config.mk", "constraint.sdc"):
                shutil.copy2(source / filename, work / filename)
            package = "/proj/workarea/yangao/rtl-certified-handoff-0002"
            command = ["podman", "run", "--rm", "--name", name, "--network", "none",
                       "-v", f"{work}:/work:Z", "-v", f"{package}:{package}:ro,Z",
                       "-e", f"NUM_CORES={args.cores}",
                       "-e", f"OMP_NUM_THREADS={args.cores}",
                       image, "bash", "-lc",
                       'cd "$ORFS_ROOT/flow" && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work']
            save(work / "launch.json", {"command": command, "started_at_utc": now()})
            checked_run(command, log, name, args.timeout)
            placements = list((work / "results").rglob("3_place.odb"))
            finals = list((work / "results").rglob("6_final.def"))
            if len(placements) != 1 or len(finals) != 1:
                raise RuntimeError(f"Expected one placement/final pair, got {placements}, {finals}")
            placement, final = placements[0], finals[0]
            record.update(input_kind="new_complete_flow_pair",
                          original_final_sha256=sha(source / "6_final.def"),
                          final_matches_original=sha(final) == sha(source / "6_final.def"),
                          signoff_status="not_rerun", flow_workspace=str(work))
        else:
            placement = source / "3_place.odb"
            final = source / "6_final.def"
            record.update(input_kind="original_run_pair",
                          signoff_status="inherited_source_record")
        shutil.copy2(final, out / "6_final.def")
        export_tcl = work / "export_place.tcl"
        export_tcl.write_text("read_db /input/3_place.odb\nwrite_def /out/3_place.def\n")
        export_name = name + "-export"
        command = ["podman", "run", "--rm", "--name", export_name, "--network", "none",
                   "-v", f"{placement}:/input/3_place.odb:ro,Z",
                   "-v", f"{export_tcl}:/export.tcl:ro,Z", "-v", f"{out}:/out:Z",
                   image, "openroad", "-no_splash", "-exit", "/export.tcl"]
        checked_run(command, log, export_name, 600)
        record.update(placement_odb=str(placement), placement_odb_sha256=sha(placement),
                      final_origin=str(final), completed_at_utc=now(),
                      seconds=round(time.monotonic() - started, 3),
                      files={f: {"sha256": sha(out / f), "bytes": (out / f).stat().st_size}
                             for f in ("3_place.def", "6_final.def", "config.mk", "constraint.sdc")})
        save(record_file, record)
        print(json.dumps({"status": "prepared", "design": design, "split": split,
                          "kind": record["input_kind"], "seconds": record["seconds"]}), flush=True)
        return record
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}", failed_at_utc=now())
        save(work / "failure.json", record)
        print(json.dumps(record), flush=True)
        return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--image", default="localhost/r2g-orfs:26Q3")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cores", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=True)
    cohort = json.loads(args.cohort.read_text())
    image = subprocess.check_output(["podman", "image", "inspect", args.image,
                                     "--format", "{{.Id}}"], text=True).strip()
    save(args.root / "cohort.json", cohort)
    save(args.root / "preparation.json", {"started_at_utc": now(), "image_id": image,
         "image_tag": args.image, "workers": args.workers, "cores_per_worker": args.cores,
         "timeout_per_design": args.timeout, "cohort_sha256": sha(args.cohort),
         "script_sha256": sha(__file__), "shared_input_preparation": True,
         "nangate45": "New complete physical flow; placement and final from same invocation; signoff not rerun"})
    records = []
    for split in ("dev", "test"):
        rows = cohort["splits"][split]
        # Cheap ODB exports first; prioritize the development split, then small
        # Nangate45 designs so canaries become available early.
        ordered = sorted(rows, key=lambda r: (r["platform"] == "nangate45", r["cells"]))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            batch = list(pool.map(lambda row: prepare_one(args, split, row, image), ordered))
        records.extend(batch)
        good = [r for r in batch if r.get("status") != "failed"]
        save(args.root / f"{split.upper()}_READY.json", {"ready": len(good) == len(rows),
             "completed_at_utc": now(), "prepared": len(good), "expected": len(rows),
             "failed": [r["design"] for r in batch if r.get("status") == "failed"]})
        print(json.dumps({"split": split, "prepared": len(good), "expected": len(rows)}), flush=True)
    good = [r for r in records if r.get("status") != "failed"]
    save(args.root / "evidence/staged_inputs.json", {"records": records, "prepared": len(good)})
    issues = [{"design": r["design"], "error": r["error"]} for r in records if r.get("status") == "failed"]
    verified = 0
    for r in good:
        for filename, meta in r["files"].items():
            p = args.root / "data" / r["split"] / r["design"] / filename
            verified += 1
            if sha(p) != meta["sha256"]:
                issues.append({"design": r["design"], "file": filename, "error": "sha256 mismatch"})
    audit = {"verified_files": verified, "issues": issues, "passed": not issues,
             "completed_at_utc": now()}
    save(args.root / "evidence/staged_inputs_audit.json", audit)
    save(args.root / "INPUTS_READY.json", {**audit, "prepared": len(good), "expected": len(records)})
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
