#!/usr/bin/env python3
"""Inventory all historical N45 repair cases without running models or EDA.

Historical categories belong only to the evaluator manifest. The cost runner
copies source material and emits a neutral design.json before calling the Agent.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--prior-manifest", type=Path, required=True)
    ap.add_argument("--prior-results", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--asset-root", type=Path)
    ap.add_argument("--restored-inputs", type=Path)
    args = ap.parse_args()
    index = load(args.index)
    prior = load(args.prior_manifest)
    old_cases = {c["id"]: c for c in prior["candidates"]}
    selected = {n: g for n, g in index["designs"].items() if g.startswith("r2g:")}
    if len(selected) != 148:
        raise ValueError(f"Expected historical 148 repair cases; found {len(selected)}")
    cases, inventory, missing = [], [], []
    for design, category in sorted(selected.items()):
        cid = design.split("__", 1)[0]
        case_id = "nangate45_" + cid
        root = (args.corpus / design).resolve()
        source_manifest = root / "design.json"
        if not source_manifest.is_file():
            raise FileNotFoundError(source_manifest)
        meta = load(source_manifest)
        recovered = []
        original_root = root
        if category == "r2g:datafile_fetch_spec" and args.asset_root and args.restored_inputs:
            asset_root = args.asset_root / meta["origin"]["repo"]
            provenance_path = asset_root / "manifest.json"
            provenance = load(provenance_path)
            identity = next(item for item in provenance["source_identities"] if item["design"] == design)
            identities = {item["path"]: item for item in identity["files"]}
            for rel in meta["files"]:
                if Path(rel).suffix.lower() in {".v", ".sv", ".vh", ".svh"}:
                    if rel not in identities or sha(root / rel) != identities[rel]["sha256"]:
                        raise ValueError(f"Original RTL does not match asset provenance: {design}/{rel}")
            restored_root = (args.restored_inputs / cid).resolve()
            shutil.copytree(root, restored_root, dirs_exist_ok=True)
            for asset in provenance["files"]:
                original = asset_root / "files" / asset["path"]
                data = original.read_bytes()
                if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                    raise ValueError(f"Asset hash mismatch: {original}")
                git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if git_blob != asset["git_blob_sha1"]:
                    raise ValueError(f"Asset Git blob mismatch: {original}")
                destination = restored_root / asset["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                recovered.append({**asset, "local_source": str(original.resolve()),
                                  "provenance_manifest": str(provenance_path.resolve()),
                                  "provenance_sha256": sha(provenance_path),
                                  "repository": provenance["repository"],
                                  "original_rtl_identity_verified": True})
            root = restored_root
            source_manifest = root / "design.json"
        absent = [f for f in meta["files"] if not (root / f).is_file()]
        hashes = {f: sha(root / f) for f in meta["files"] if (root / f).is_file()}
        hashes.update({item["path"]: item["sha256"] for item in recovered})
        all_hashes = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*"))
                      if p.is_file() and p.name != "design.json" and ".git" not in p.parts}
        references = []
        for rel in meta["files"]:
            p = root / rel
            if not p.is_file() or p.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
                continue
            for ref in re.findall(r'\$readmem[hb]\s*\(\s*"([^"\n]+)"', p.read_text(errors="replace")):
                exact = [str(q.relative_to(root)) for q in [root / ref, p.parent / ref]
                         if q.is_file() and q.is_relative_to(root)]
                basename = [r for r in all_hashes if Path(r).name == Path(ref).name]
                references.append({"rtl": rel, "reference": ref,
                                   "exact_relative_candidates": sorted(set(exact)),
                                   "basename_candidates": basename,
                                   "resolution_proven": False,
                                   "note": "Static inventory only; actual frontend resolves runtime working directory."})
        old = old_cases.get(case_id, {})
        prior_folder = args.prior_results / case_id
        unresolved_assets = [r["reference"] for r in references
                             if not r["basename_candidates"] and not r["exact_relative_candidates"]]
        case = {
            "id": case_id, "platform": "nangate45", "design": design,
            "top": meta["top"], "input_root": str(root),
            "input_kind": "original_unrepaired_corpus",
            "source_manifest": str(source_manifest), "source_files": meta["files"],
            "include_dirs": meta.get("include_dirs", []), "source_sha256": hashes,
            "source_manifest_sha256": sha(source_manifest),
            "original_corpus_root": str(original_root),
            "authentic_assets_restored": recovered,
            "historical_category": category, "preinject_recipe": False,
            "baseline_remote_run": "/proj/workarea/yangao/rerun_nangate45_20261018/runs/"
                                   + meta["origin"]["baseline_run"],
            "remote_original_source_hash_matches": old.get("remote_original_source_hash_matches"),
            "source_hash_mismatches": old.get("source_hash_mismatches", []),
            "source_inventory_complete": not absent and not meta.get("origin", {}).get("missing"),
            "missing_source_files": absent,
            "static_readmem_assets_not_found": unresolved_assets,
            "input_asset_review_required": bool(unresolved_assets),
            "asset_policy": "Recover authentic original repository assets before launch; never fabricate ROM content.",
            "prior_api_evidence_dir": str(prior_folder.resolve()) if prior_folder.is_dir() else None,
            "reuse_prior_api_measurement_if_complete": prior_folder.is_dir(),
            "semantic_guard": {
                "known_aldff_counterexample": cid in {"C01169", "C03509"},
                "allow_semantics_changing_transform": False,
                "required_behavior": "Use original RTL and normal safety guards; report unrepaired failure honestly."
            },
            "protocol_note": "Current-system API replay, original input, current platform defaults. "
                             "The historical p2 baseline is an earlier fixed-period run, not final formula-spec repair. "
                             "Evaluator category and prior outcome must not enter model prompt."
        }
        cases.append(case)
        inventory.append({"id": case_id, "file_count": len(all_hashes),
                          "sha256": all_hashes, "origin_missing": meta.get("origin", {}).get("missing", []),
                          "missing_listed_sources": absent, "readmem_references": references})
        if absent:
            missing.append({"id": case_id, "missing": absent})
    manifest = {
        "schema": "api-repair-cost-candidates-v1",
        "purpose": "All 148 historical N45 physical repair cases through real API Agent; no sampling",
        "evidence_role": prior["evidence_role"], "execution_policy": prior["execution_policy"],
        "historical_population": {"nangate45": {"physical_repairs": 148, "backend": 128, "frontend": 20,
                                    "known_semantic_counterexamples_included_as_original_safe_attempts": ["C01169", "C03509"]}},
        "candidates": cases
    }
    audit = {"created_utc": datetime.now(timezone.utc).isoformat(),
             "index": str(args.index.resolve()), "index_sha256": sha(args.index),
             "corpus": str(args.corpus.resolve()), "total": len(cases),
             "category_counts": dict(sorted(Counter(selected.values()).items())),
             "source_file_missing_cases": missing,
             "input_asset_review_cases": [{"id": c["id"], "references": c["static_readmem_assets_not_found"]}
                                          for c in cases if c["input_asset_review_required"]],
             "prior_api_case_ids": [c["id"] for c in cases if c["prior_api_evidence_dir"]],
             "hash_evidence_scope": "Local original corpus freeze; only previously audited examples have remote hash comparison evidence.",
             "cases": inventory}
    save(args.output, manifest)
    audit_path = args.output.with_name("nangate45_inventory_audit.json")
    save(audit_path, audit)
    print(json.dumps({"total": len(cases), "missing_cases": len(missing),
                      "prior_api_cases": len(audit["prior_api_case_ids"]),
                      "manifest": str(args.output), "audit": str(audit_path)}))


if __name__ == "__main__":
    main()
