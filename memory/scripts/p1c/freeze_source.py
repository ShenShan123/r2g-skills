#!/usr/bin/env python3
"""Freeze SOURCE v1 from the post-recovery export (feature-freeze phase, step 1).
Usage: freeze_source.py <code root> <post-recovery export dir> <out dir>"""
import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path

code, exp, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code / "memory"))
from tehm import neutral_export as nx  # noqa: E402

FAMILY_OF_DESIGN = {  # proposed lineage families (RTL header evidence; see the P1c plan section 3g)
    "aes_core": "secworks/aes", "chacha_core": "secworks/chacha", "sha256_core": "secworks/sha256",
    "des_area": "opencores/des", "i2c_master_top": "opencores/i2c", "picorv32": "yosyshq/picorv32",
    "riscv_core": "ultraembedded/riscv", "eth_demux": "alexforencich/verilog-ethernet",
    "udp_ip_rx_64": "alexforencich/verilog-ethernet"}
FAMILIES = sorted(set(FAMILY_OF_DESIGN.values()))


def exclusion(u):
    if not u["source_eligible"]:
        return "copies_conflict_" + (u["conflict"] or {}).get("kind", "?")
    sit = u["raw_row"].get("situation") or {}
    unknown = sorted(k for k, v in sit.items() if v == nx.UNKNOWN)
    if unknown:
        return "situation_unknown:" + ",".join(unknown)
    positive = u["outcome"]["verified"] and u["outcome"]["canonical"] in ("PASS", "PARTIAL")
    if positive and u["raw_row"].get("rolled_back") is None:
        return "verified_positive_rollback_status_undetermined"
    if u["identity"]["design"] not in FAMILY_OF_DESIGN:
        return "design_without_family"
    return None


units = nx.read_export(exp)
manifest_in = json.loads((exp / "source_manifest.json").read_text())
keep, excluded = [], []
for u in units:
    why = exclusion(u)
    (excluded.append({"unit_id": u["unit_id"], "reason": why, "design": u["identity"]["design"]}) if why
     else keep.append(u))
split = {"family_of_design": FAMILY_OF_DESIGN, "SOURCE": FAMILIES, "DEV": FAMILIES, "PROBE": [], "TEST": [],
         "exposed_in_development": FAMILIES,
         "note": "all 8 families were used in development: they supply SOURCE (DEV overlap disclosed) and DEV only; "
                 "PROBE/TEST must come from new families (pending the user's selection)"}
iso = nx.check_isolation(keep, split)
if not iso["ok"]:
    raise SystemExit("isolation check failed: %s" % iso["errors"])
commit = subprocess.run(["git", "-C", str(code), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
m = nx.write_export(keep, out, dedup_log=manifest_in["dedup_log"], meta={
    "name": "SOURCE-v1", "status": "frozen", "code_commit": commit,
    "post_recovery_export_sha256": manifest_in["export_sha256"],
    "restoration_review_sha256": manifest_in.get("restoration_review_sha256"),
    "annotation_converter": nx.ANNOTATION_CONVERTER, "split": split, "isolation": iso,
    "excluded_counts": dict(collections.Counter(e["reason"].split(":")[0] for e in excluded))})
(out / "excluded_units.json").write_text(json.dumps(excluded, indent=1) + "\n")
pos = [u for u in keep if u["outcome"]["verified"] and u["outcome"]["canonical"] in ("PASS", "PARTIAL")]
print("SOURCE-v1: %d units kept, %d excluded %s" % (len(keep), len(excluded), dict(collections.Counter(
    e["reason"].split(":")[0] for e in excluded))))
print("kept by family:", iso["units_per_family"])
print("kept by class: %s; verified positives %d (rollback restored %d, recorded %d)" % (
    dict(collections.Counter(u["outcome"]["canonical"] for u in keep)), len(pos),
    sum(1 for u in pos if u["raw_row"].get("rollback_restoration")),
    sum(1 for u in pos if u["raw_row"].get("rolled_back") is not None and not u["raw_row"].get("rollback_restoration"))))
print("export sha256", m["export_sha256"])
