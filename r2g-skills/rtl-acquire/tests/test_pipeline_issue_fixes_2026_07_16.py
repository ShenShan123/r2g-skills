"""Regressions for the 2026-07-16 full-pipeline issue-report fixes (issues
1,2,3,4,5,8,10,11 — the rtl-acquire half). Each test guards one probe from
docs/superpowers/plans/2026-07-16-full-pipeline-issue-report.md."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
for sub in ("", "execute", "acquire", "promote", "publish", "repair"):
    p = SCRIPTS / sub if sub else SCRIPTS
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import clone_repo_manifest as crm                     # noqa: E402
import expand_candidates as xc                        # noqa: E402
import promote_candidates as pc                       # noqa: E402
from common.clock_infer import infer_clock_ports      # noqa: E402
from classify_failed_candidates import classify       # noqa: E402
from discover_download_candidates import bundle_closure  # noqa: E402


# ── Issue 4: reject can never be publish-eligible ────────────────────────────

def test_shipped_policy_has_no_reject_action():
    policy = json.loads((SKILL_ROOT / "references" / "publish_policy.json")
                        .read_text(encoding="utf-8"))
    assert "reject" not in [a.lower() for a in policy["allowed_design_actions"]]
    assert policy.get("allowed_license_status") == ["allow"]
    assert policy.get("require_source_commit") is True


def test_policy_loader_rejects_terminal_action(tmp_path):
    import build_publish_candidates as bpc
    bad = {"allowed_design_actions": ["keep", "reject"]}
    with pytest.raises(SystemExit):
        bpc.load_allowed_actions(bad, tmp_path / "p.json")


# ── Issue 2: license + resolved commit ───────────────────────────────────────

def test_classify_license_verdicts(tmp_path):
    mit = tmp_path / "mit"; mit.mkdir()
    (mit / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, "
                                 "free of charge...", encoding="utf-8")
    assert crm.classify_license(mit)[0] == "allow"
    gpl = tmp_path / "gpl"; gpl.mkdir()
    (gpl / "COPYING").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3",
                                 encoding="utf-8")
    assert crm.classify_license(gpl)[0] == "deny"
    spdx = tmp_path / "spdx"; spdx.mkdir()
    (spdx / "LICENSE.txt").write_text("SPDX-License-Identifier: Apache-2.0\n",
                                      encoding="utf-8")
    assert crm.classify_license(spdx)[0] == "allow"
    none = tmp_path / "none"; none.mkdir()
    assert crm.classify_license(none) == ("unknown", "no_license_file")


def test_resolved_commit_from_local_git(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "a.v").write_text("module a; endmodule\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    commit = crm.resolved_commit(repo)
    assert len(commit) == 40
    assert crm.resolved_commit(tmp_path) == ""     # non-git tree: honest empty


def test_publish_gate_blocks_unknown_license_and_missing_commit(tmp_path):
    """End-to-end gate: unknown license blocked; cloned repo without commit
    blocked; allow+commit eligible."""
    root = tmp_path
    idx = root / "index.csv"
    idx.write_text("design,status\nlic_ok,success\nlic_unknown,success\n"
                   "no_commit,success\n", encoding="utf-8")
    scores = root / "scores.csv"
    hdr = ("design,design_action,design_quality_score,graph_complexity_score,"
           "dominant_cell_share,low_fidelity\n")
    scores.write_text(hdr + "lic_ok,keep,0.9,0.4,0.2,False\n"
                            "lic_unknown,keep,0.9,0.4,0.2,False\n"
                            "no_commit,keep,0.9,0.4,0.2,False\n", encoding="utf-8")
    metas = {
        "lic_ok": {"source_kind": "cloned_repo", "source_commit": "a" * 40,
                   "license_status": "allow"},
        "lic_unknown": {"source_kind": "cloned_repo", "source_commit": "b" * 40},
        "no_commit": {"source_kind": "cloned_repo", "source_commit": "",
                      "license_status": "allow"},
    }
    for d, m in metas.items():
        (root / d).mkdir()
        (root / d / "design_meta.json").write_text(json.dumps(m), encoding="utf-8")
    policy = root / "policy.json"
    policy.write_text(json.dumps({"allowed_design_actions": ["keep", "conditional"],
                                  "min_nontrivial_complexity_score": 0.02}),
                      encoding="utf-8")
    out = root / "out.csv"
    subprocess.run([sys.executable,
                    str(SCRIPTS / "publish" / "build_publish_candidates.py"),
                    "--external-index", str(idx), "--design-scores", str(scores),
                    "--publish-policy-json", str(policy), "--out-csv", str(out),
                    "--out-json", str(root / "o.json"), "--out-md", str(root / "o.md")],
                   check=True, capture_output=True)
    import csv as _csv
    rows = {r["design"]: r for r in _csv.DictReader(out.open(encoding="utf-8"))}
    assert rows["lic_ok"]["publish_eligible"] == "True"
    assert rows["lic_unknown"]["publish_eligible"] == "False"
    assert "license_status=unknown" in rows["lic_unknown"]["publish_reasons"]
    assert rows["no_commit"]["publish_eligible"] == "False"
    assert "missing_source_commit" in rows["no_commit"]["publish_reasons"]


# ── Issue 3: failed synth cannot be reconstructed as success ─────────────────

def test_synthesize_rejects_stale_netlist_and_nonzero_rc(tmp_path, monkeypatch):
    proj = tmp_path / "proj"; proj.mkdir()
    flow = tmp_path / "flow"
    results = flow / "results" / "nangate45" / "top1" / "d1"
    results.mkdir(parents=True)
    stale = results / "1_2_yosys.v"
    stale.write_text("// stale netlist from a prior run\n", encoding="utf-8")
    old = 1_600_000_000.0
    os.utime(stale, (old, old))
    monkeypatch.setattr(xc, "default_flow_dir", lambda: flow)
    monkeypatch.setattr(xc, "run", lambda *a, **k: SimpleNamespace(returncode=7))
    rc, netlist, _ = xc.synthesize(proj, "d1", "top1")
    assert rc == 7 and netlist is None          # stale artifact never = success
    # rc==0 with a FRESH netlist is still accepted
    stale.touch()
    monkeypatch.setattr(xc, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    rc, netlist, _ = xc.synthesize(proj, "d1", "top1")
    assert rc == 0 and netlist is not None


def test_rebuild_index_honors_meta_synth_failed(tmp_path):
    ddir = tmp_path / "corpus" / "d1"
    ddir.mkdir(parents=True)
    (ddir / "mapped_netlist.v").write_text("m\n", encoding="utf-8")
    (ddir / "netlist_graph.pt").write_bytes(b"pt")
    (ddir / "design_meta.json").write_text(json.dumps(
        {"status": "synth_failed", "top": "t"}), encoding="utf-8")
    out = tmp_path / "index.csv"
    subprocess.run([sys.executable,
                    str(SCRIPTS / "publish" / "rebuild_external_index_from_dirs.py"),
                    "--root", str(tmp_path / "corpus"), "--index", str(out)],
                   check=True, capture_output=True)
    import csv as _csv
    rows = {r["design"]: r for r in _csv.DictReader(out.open(encoding="utf-8"))}
    assert rows["d1"]["status"] == "synth_failed"   # stale artifacts don't win


# ── Issue 1: promotion bound to the synth-proven bytes ───────────────────────

def _mini_candidate(tmp_path, *, mutate_after_manifest=False):
    rtl = tmp_path / "src" / "top.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input Clk, output reg q);\n"
                   "always @(posedge Clk) q <= 1'b1;\nendmodule\n",
                   encoding="utf-8")
    manifest = xc._source_manifest([rtl])
    if mutate_after_manifest:
        rtl.write_text("module top(input Clk, output q);\nassign q = 1'b0;\n"
                       "endmodule\n", encoding="utf-8")
    out_root = tmp_path / "corpus"
    (out_root / "d1").mkdir(parents=True)
    meta = {"status": "success", "top": "top", "rtl_files": [str(rtl)],
            "source_manifest": manifest, "platform": "nangate45"}
    (out_root / "d1" / "design_meta.json").write_text(json.dumps(meta),
                                                      encoding="utf-8")
    return out_root


def _promote_args(**over):
    base = dict(platform="", clock_port="", clock_period=10.0,
                core_utilization=20, place_density=0.20, force=False,
                dry_run=True, run=False, allow_virtual_clock=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_promote_refuses_mutated_rtl_bytes(tmp_path):
    out_root = _mini_candidate(tmp_path, mutate_after_manifest=True)
    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_promote_args(), index_row={"status": "success"})
    assert res["status"] == "rtl_bytes_changed_since_synth"
    assert not (tmp_path / "cases" / "d1").exists()     # nothing materialized


def test_promote_verifies_unchanged_bytes(tmp_path):
    out_root = _mini_candidate(tmp_path)
    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_promote_args(), index_row={"status": "success"})
    assert res["status"] == "would_promote"
    assert res["source_bytes_verified"] is True


# ── Issue 5: sequential design + virtual clock is gated ─────────────────────

def test_detect_clock_port_infers_nonstandard_clock(tmp_path):
    rtl = tmp_path / "eth.v"
    rtl.write_text("module ethtop(input MTxClk, input Reset, output reg q);\n"
                   "always @(posedge MTxClk or posedge Reset) q <= 1'b1;\n"
                   "always @(posedge MTxClk) q <= q;\nendmodule\n",
                   encoding="utf-8")
    assert pc.detect_clock_port("ethtop", [rtl]) == "MTxClk"
    assert infer_clock_ports("ethtop", [rtl.read_text()]) == ["MTxClk"]


def test_promote_rejects_sequential_virtual_clock(tmp_path):
    out_root = _mini_candidate(tmp_path)
    # strip the clock: a top with seq cells but NO resolvable clock port
    meta_p = out_root / "d1" / "design_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    rtl = Path(meta["rtl_files"][0])
    rtl.write_text("module top(input [3:0] d, output q);\nassign q = ^d;\n"
                   "endmodule\n", encoding="utf-8")
    meta["source_manifest"] = xc._source_manifest([rtl])
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_promote_args(),
                         index_row={"status": "success", "seq_cells": "119"})
    assert res["status"] == "rejected_unconstrained_clock"
    # explicit override promotes (dry-run)
    res2 = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                          args=_promote_args(allow_virtual_clock=True),
                          index_row={"status": "success", "seq_cells": "119"})
    assert res2["status"] == "would_promote"
    # combinational designs keep the virtual-clock path
    res3 = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                          args=_promote_args(),
                          index_row={"status": "success", "seq_cells": "0"})
    assert res3["status"] == "would_promote"


# ── Issue 8: filesystem containment ──────────────────────────────────────────

def _tar_bytes(members: list[tuple[str, bytes]], links: list[tuple[str, str]] = ()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return buf.getvalue()


def test_malicious_tar_members_rejected(tmp_path):
    root = tmp_path / "extract"; root.mkdir()
    outside = tmp_path / "PWNED"
    # traversal member + escaping symlink: must RAISE, nothing written outside
    for tar in (_tar_bytes([("../PWNED", b"x")]),
                _tar_bytes([("ok/a.v", b"x")], links=[("ok/l", "../../PWNED")])):
        p = tmp_path / "a.tgz"
        p.write_bytes(tar)
        with tarfile.open(p) as tf:
            with pytest.raises(Exception):
                crm._safe_extract_tar(tf, root)
    # absolute member: PEP-706 data filter STRIPS the leading slash (contained,
    # no raise) — the invariant is that nothing lands outside the target root
    (tmp_path / "abs.tgz").write_bytes(_tar_bytes([("/abs/PWNED", b"x")]))
    with tarfile.open(tmp_path / "abs.tgz") as tf:
        try:
            crm._safe_extract_tar(tf, root)
        except Exception:
            pass                                  # refusing outright is also fine
    assert not outside.exists()
    assert not Path("/abs/PWNED").exists()
    good = _tar_bytes([("repo/x.v", b"module x; endmodule\n")])
    (tmp_path / "g.tgz").write_bytes(good)
    with tarfile.open(tmp_path / "g.tgz") as tf:
        crm._safe_extract_tar(tf, root)
    assert (root / "repo" / "x.v").is_file()


def test_zip_symlink_member_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("evil_link")
        info.external_attr = (0o120777 << 16)      # symlink mode bits
        zf.writestr(info, "/etc/passwd")
    p = tmp_path / "e.zip"
    p.write_bytes(buf.getvalue())
    with zipfile.ZipFile(p) as zf:
        with pytest.raises(ValueError):
            crm._safe_extract_zip(zf, tmp_path / "x")


# ── Issue 10: closure truncation is visible + retried ────────────────────────

def test_bundle_closure_reports_unresolved_on_cap(tmp_path):
    files = {}
    mod2path = {}
    for i in range(18):
        p = tmp_path / f"m{i}.v"
        files[p] = {"local_refs": {f"m{i+1}"} if i < 17 else set()}
        mod2path[f"m{i}"] = p
    ordered, unresolved = bundle_closure(tmp_path / "m0.v", files, mod2path,
                                         max_files=16)
    assert len(ordered) == 16
    assert unresolved and "m16" in unresolved     # the cut tail is NAMED
    ordered2, unresolved2 = bundle_closure(tmp_path / "m0.v", files, mod2path,
                                           max_files=64)
    assert len(ordered2) == 18 and unresolved2 == []


def test_classifier_retries_truncated_bundle_missing_module():
    notes = ("synth failed: ERROR: module `eth_random' not found; "
             "bundle_incomplete=2; unresolved=eth_random+eth_defines")
    assert classify("/d/ethmac/eth_top.v", notes) == ("retry", "missing_local_module")
    # a genuinely-external missing module (no marker) keeps the old exclusion
    action, _ = classify("/d/x/top.v", "ERROR: module `vendor_ip' not found")
    assert action == "exclude"


# ── Issue 11: quality schema honesty ─────────────────────────────────────────

def test_graph_stats_emits_cell_histogram(tmp_path):
    """The producer must emit the histogram the scorer's entropy/redundancy
    metrics consume (they silently zeroed without it)."""
    src = (SCRIPTS / "execute" / "graph_stats.py").read_text(encoding="utf-8")
    assert 'stats["cell_histogram"]' in src


def test_scorer_blocks_absent_histogram(tmp_path):
    import csv as _csv
    root = tmp_path
    (root / "with_hist").mkdir()
    (root / "no_hist").mkdir()
    (root / "with_hist" / "cell_stats.json").write_text(json.dumps(
        {"cells": 100, "cell_histogram": {"INV_X1": 95, "NAND2_X1": 5},
         "graph_dominant_gate_share": 0.95}), encoding="utf-8")
    (root / "no_hist" / "cell_stats.json").write_text(json.dumps(
        {"cells": 100, "graph_dominant_gate_share": 0.4}), encoding="utf-8")
    index = root / "index.csv"
    index.write_text("design,status\nwith_hist,success\nno_hist,success\n",
                     encoding="utf-8")
    out = root / "quality.csv"
    r = subprocess.run([sys.executable,
                        str(SCRIPTS / "report" / "score_design_quality.py"),
                        "--external-index", str(index),
                        "--external-root", str(root),
                        "--out-csv", str(out),
                        "--out-json", str(root / "q.json"),
                        "--out-md", str(root / "q.md")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = {x["design"]: x for x in _csv.DictReader(out.open(encoding="utf-8"))}
    assert rows["no_hist"]["design_action"] == "conditional"
    assert "stats_schema_missing:cell_histogram" in rows["no_hist"]["quality_notes"]
    assert rows["with_hist"]["quality_notes"] == ""
