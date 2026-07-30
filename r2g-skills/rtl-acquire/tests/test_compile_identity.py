"""Frozen compilation inputs, vendored headers, relocatable corpora.

2026-07-19 post-consolidation audit P0-N2 / P0-R5 / P1-N6 (failure-patterns.md
#52) — the rtl-acquire third of the round's shared root cause, identity inferred
from mutable paths and files rather than carried:

  * **P0-N2** — promotion re-parsed the synth project's MUTABLE `config.mk` for
    top parameters, frontend and memory settings. The audit synth-qualified a
    design at `WIDTH 8`, edited config.mk to `WIDTH 16` without touching one RTL
    byte, and promotion carried WIDTH=16 into the full flow while reporting
    `source_bytes_verified=true`. A different circuit, positively claiming
    verification.
  * **P0-R5** — `source_manifest` covered `source_files` only and `vendor_rtl`
    copied only those, so ``include`d headers were neither digested nor
    vendored; promotion then carried the EXTERNAL synth-time include dirs into
    the promoted config. Four of eight successful 2026-07-16 candidates depended
    on such headers.
  * **P1-N6** — candidate metadata treats acquisition-time ABSOLUTE paths as
    authoritative forever. All 708 design_meta.json in this checkout point at
    `/home/yuany/...` (absent here) while all 708 have a complete local `rtl/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS / "promote"))
sys.path.insert(0, str(_SCRIPTS / "execute"))

import promote_candidates as pc  # noqa: E402


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _corpus(tmp_path, *, rtl_body="module top(); endmodule\n",
            recorded_prefix="/nonexistent/home/other", compile_manifest=None,
            headers=None):
    """A corpus entry whose recorded paths are UNREACHABLE but whose vendored
    rtl/ is complete — the shape every one of the 708 local candidates has."""
    out_root = tmp_path / "corpus"
    cdir = out_root / "d1"
    (cdir / "rtl").mkdir(parents=True)
    (cdir / "rtl" / "top.v").write_text(rtl_body)
    recorded = f"{recorded_prefix}/top.v"
    meta = {
        "design": "d1", "top": "top", "status": "success",
        "platform": "nangate45", "seq_cells": 0,
        "rtl_files": [recorded],
        "source_manifest": [{"path": recorded, "size": len(rtl_body),
                             "sha256": hashlib.sha256(rtl_body.encode()).hexdigest()}],
        "design_config": str(cdir / "synth_config.mk"),
    }
    if compile_manifest is not None:
        meta["compile_manifest"] = compile_manifest
    for name, body in (headers or {}).items():
        (cdir / "rtl" / name).write_text(body)
    (cdir / "design_meta.json").write_text(json.dumps(meta))
    (cdir / "synth_config.mk").write_text("export PLATFORM = nangate45\n")
    return out_root, cdir


def _args(**kw):
    base = dict(clock_port="", clock_period=10.0, allow_virtual_clock=True, platform="",
                force=True, dry_run=True, allow_unverified_source=False,
                core_utilization=30, place_density=0.20, run=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# P1-N6 — a relocated corpus promotes from its own vendored RTL.               #
# --------------------------------------------------------------------------- #

def test_relocated_corpus_resolves_from_vendored_rtl(tmp_path):
    out_root, cdir = _corpus(tmp_path)
    resolved = pc.resolve_candidate_rtl(["/nonexistent/home/other/top.v"], cdir)

    assert len(resolved) == 1
    assert resolved[0]["source"] == "vendored"
    assert resolved[0]["path"] == cdir / "rtl" / "top.v"
    # The manifest KEY stays the recorded path — relocation must not become a way
    # to launder a byte change past the digest check.
    assert resolved[0]["key"] == "/nonexistent/home/other/top.v"


def test_nested_vendored_tree_matches_by_longest_path_tail(tmp_path):
    """Not every candidate vendors a FLAT rtl/. 34 of the 708 local candidates
    keep a nested tree, and several share a basename across subdirectories — so
    the longest matching path tail, not the basename, picks the right file."""
    out_root, cdir = _corpus(tmp_path)
    for sub in ("pkt_part", "sim_rtl"):
        (cdir / "rtl" / "peripherals_part" / sub).mkdir(parents=True)
        (cdir / "rtl" / "peripherals_part" / sub / "syncfifo.v").write_text(
            f"// {sub}\n")

    resolved = pc.resolve_candidate_rtl(
        ["/gone/src/peripherals_part/pkt_part/syncfifo.v"], cdir)
    assert resolved[0]["source"] == "vendored"
    assert resolved[0]["path"].read_text() == "// pkt_part\n"     # NOT sim_rtl


def test_ambiguous_basename_is_refused_not_guessed(tmp_path):
    """Two same-named files in different subdirs and no path tail to disambiguate:
    guessing would be a silent wrong-value defect, so refuse."""
    out_root, cdir = _corpus(tmp_path)
    for sub in ("a", "b"):
        (cdir / "rtl" / sub).mkdir(parents=True)
        (cdir / "rtl" / sub / "shared.v").write_text(f"// {sub}\n")

    resolved = pc.resolve_candidate_rtl(["/gone/shared.v"], cdir)
    assert resolved[0]["path"] is None
    assert resolved[0]["source"] == "ambiguous"
    assert len(resolved[0]["candidates"]) == 2


def test_recorded_path_still_wins_when_reachable(tmp_path):
    out_root, cdir = _corpus(tmp_path)
    real = tmp_path / "elsewhere" / "top.v"
    real.parent.mkdir()
    real.write_text("module top(); endmodule\n")

    resolved = pc.resolve_candidate_rtl([str(real)], cdir)
    assert resolved[0]["source"] == "recorded" and resolved[0]["path"] == real


def test_relocated_promotion_succeeds_and_is_flagged(tmp_path):
    out_root, cdir = _corpus(tmp_path)
    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "would_promote", res
    assert res["source_bytes_verified"] is True
    assert res["source_relocated"] == 1


def test_relocated_bytes_that_changed_are_still_blocked(tmp_path):
    """Resolving from the vendored copy must not weaken the byte check."""
    out_root, cdir = _corpus(tmp_path)
    (cdir / "rtl" / "top.v").write_text("module top(); wire tampered; endmodule\n")

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "rtl_bytes_changed_since_synth", res


def test_unresolvable_rtl_is_a_structured_failure(tmp_path):
    out_root = tmp_path / "corpus"
    cdir = out_root / "d1"
    cdir.mkdir(parents=True)
    (cdir / "design_meta.json").write_text(json.dumps(
        {"design": "d1", "top": "top", "status": "success",
         "rtl_files": ["/nonexistent/gone.v"]}))

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success"})
    assert res["status"] == "rtl_files_unresolved"
    assert "gone.v" in res["reason"]


def test_unreadable_path_is_absent_not_fatal(tmp_path, monkeypatch):
    """Path.is_file() propagates EACCES; a relocated corpus on an unreadable
    mount used to raise PermissionError out of promote_one and kill --all."""
    def boom(self):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "is_file", boom)
    assert pc._readable_file(Path("/whatever")) is False


# --------------------------------------------------------------------------- #
# P0-N2 — compilation inputs are frozen at synth time.                         #
# --------------------------------------------------------------------------- #

_MAN = {"top": "top", "include_dirs": [], "top_parameters": {"WIDTH": "8"},
        "defines": {}, "synth_frontend": None, "synth_memory_max_bits": None,
        "synth_variant": None, "header_manifest": [], "config_digest": "abc"}


def test_mutated_top_params_block_promotion(tmp_path):
    """The audit's exact reproduction: RTL bytes untouched, WIDTH 8 -> 16."""
    out_root, cdir = _corpus(tmp_path, compile_manifest=_MAN)
    (cdir / "synth_config.mk").write_text(
        "export PLATFORM = nangate45\nexport VERILOG_TOP_PARAMS = WIDTH 16\n")

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "compile_inputs_changed_since_synth", res
    assert "WIDTH" in res["reason"]


def test_matching_top_params_promote_and_are_verified(tmp_path):
    out_root, cdir = _corpus(tmp_path, compile_manifest=_MAN)
    (cdir / "synth_config.mk").write_text(
        "export PLATFORM = nangate45\nexport VERILOG_TOP_PARAMS = WIDTH 8\n")

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "would_promote", res
    assert res["compile_inputs_verified"] is True


def test_mutated_frontend_blocks_promotion(tmp_path):
    man = dict(_MAN, synth_frontend="slang", top_parameters={})
    out_root, cdir = _corpus(tmp_path, compile_manifest=man)
    (cdir / "synth_config.mk").write_text(
        "export PLATFORM = nangate45\nexport SYNTH_HDL_FRONTEND = surelog\n")

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "compile_inputs_changed_since_synth", res


def test_legacy_candidate_without_compile_manifest_is_marked_unverified(tmp_path):
    out_root, cdir = _corpus(tmp_path)      # no compile_manifest
    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "would_promote"
    assert res["compile_inputs_verified"] is False


# --------------------------------------------------------------------------- #
# P0-R5 — the header closure is captured, digested and vendored.               #
# --------------------------------------------------------------------------- #

def test_header_closure_follows_includes_transitively(tmp_path):
    import expand_candidates as ec

    src = tmp_path / "src"
    inc = tmp_path / "inc"
    src.mkdir()
    inc.mkdir()
    (src / "top.v").write_text('`include "defs.vh"\nmodule top(); endmodule\n')
    (inc / "defs.vh").write_text('`include "timescale.vh"\n`define W 8\n')
    (inc / "timescale.vh").write_text("`timescale 1ns/1ps\n")
    (inc / "unused.vh").write_text("// never included\n")

    closure = ec._header_closure([src / "top.v"], [inc])
    names = sorted(p.name for p in closure)
    assert names == ["defs.vh", "timescale.vh"]      # transitive, and no unused.vh


def test_compile_manifest_digests_headers_and_normalizes_config(tmp_path):
    import expand_candidates as ec

    src = tmp_path / "src"
    src.mkdir()
    (src / "top.v").write_text('`include "defs.vh"\nmodule top(); endmodule\n')
    (src / "defs.vh").write_text("`define W 8\n")

    man = ec._compile_manifest([src / "top.v"], [src], top="top",
                               top_parameters={"WIDTH": "8"}, synth_frontend=None,
                               synth_memory_max_bits=None, synth_variant="area")
    assert [e["path"] for e in man["header_manifest"]] == [str(src / "defs.vh")]
    assert man["header_manifest"][0]["sha256"] == _digest(src / "defs.vh")

    # The digest covers the semantic inputs and is order-insensitive...
    same = ec._compile_manifest([src / "top.v"], [src], top="top",
                                top_parameters={"WIDTH": "8"}, synth_frontend=None,
                                synth_memory_max_bits=None, synth_variant="area")
    assert same["config_digest"] == man["config_digest"]
    # ...but moves when a real input changes.
    diff = ec._compile_manifest([src / "top.v"], [src], top="top",
                                top_parameters={"WIDTH": "16"}, synth_frontend=None,
                                synth_memory_max_bits=None, synth_variant="area")
    assert diff["config_digest"] != man["config_digest"]


def test_compile_manifest_freezes_readmem_payload(tmp_path):
    import expand_candidates as ec

    repo = tmp_path / "repo"
    rtl = repo / "rtl"
    rtl.mkdir(parents=True)
    top = rtl / "top.v"
    payload = rtl / "init.mem"
    top.write_text(
        'module top; reg [7:0] mem [0:1]; initial $readmemh("init.mem", mem); '
        "endmodule\n")
    payload.write_text("00\nff\n")

    man = ec._compile_manifest(
        [top], [rtl], top="top", top_parameters={},
        synth_frontend=None, synth_memory_max_bits=None,
        synth_variant="yosys_abc_area0", repo_dir=repo)

    assert man["unresolved_collateral"] == []
    assert len(man["collateral_manifest"]) == 1
    entry = man["collateral_manifest"][0]
    assert entry["reference"] == "init.mem"
    assert entry["sha256"] == _digest(payload)


def test_dynamic_readmem_path_is_an_unresolved_compile_input(tmp_path):
    import expand_candidates as ec

    top = tmp_path / "top.v"
    top.write_text(
        "module top(input [8*8-1:0] file); reg [7:0] mem [0:1]; "
        "initial $readmemh(file, mem); endmodule\n")
    man = ec._compile_manifest(
        [top], [tmp_path], top="top", top_parameters={},
        synth_frontend=None, synth_memory_max_bits=None,
        synth_variant="yosys_abc_area0", repo_dir=tmp_path)
    assert man["unresolved_collateral"][0]["reason"] == "dynamic_readmem_path"


def test_transformed_candidate_without_original_lineage_is_blocked(tmp_path):
    out_root, cdir = _corpus(tmp_path)
    meta_path = cdir / "design_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["sv2v_fallback_used"] = True
    meta_path.write_text(json.dumps(meta))

    res = pc.promote_one(
        "d1", out_root=out_root, base_dir=tmp_path / "cases",
        args=_args(), index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "transformation_lineage_missing"


def test_transformation_manifest_binds_original_and_generated_bytes(
        tmp_path, monkeypatch):
    import expand_candidates as ec

    original = tmp_path / "top.sv"
    generated = tmp_path / "top.v"
    original.write_text("module top(input logic a); endmodule\n")
    generated.write_text("module top(input a); endmodule\n")
    monkeypatch.setattr(ec, "sv2v_path", lambda: None)
    manifest = ec._transformation_manifest(
        [original], [generated], "sv2v", [], "top")
    assert manifest["required"] is True
    assert manifest["kind"] == "sv2v"
    assert manifest["original_manifest"][0]["sha256"] == _digest(original)
    assert manifest["compiled_manifest"][0]["sha256"] == _digest(generated)
    assert manifest["lineage_digest"]


def test_promotion_vendors_and_rebinds_readmem_payload(tmp_path):
    import expand_candidates as ec

    body = ('module top; reg [7:0] mem [0:1]; '
            'initial $readmemh("init.mem", mem); endmodule\n')
    out_root, cdir = _corpus(tmp_path, rtl_body=body)
    payload = cdir / "rtl" / "init.mem"
    payload.write_text("00\nff\n")
    top = cdir / "rtl" / "top.v"
    man = ec._compile_manifest(
        [top], [top.parent], top="top", top_parameters={},
        synth_frontend=None, synth_memory_max_bits=None,
        synth_variant="yosys_abc_area0", repo_dir=cdir)
    meta_path = cdir / "design_meta.json"
    meta = json.loads(meta_path.read_text())
    # The real expansion records the same compile-time consumer identity in the
    # source and collateral manifests. Simulate that identity before relocating
    # the corpus entry to its vendored rtl/ copy.
    man["collateral_manifest"][0]["consumer"] = meta["source_manifest"][0]["path"]
    meta["compile_manifest"] = man
    meta_path.write_text(json.dumps(meta))

    res = pc.promote_one(
        "d1", out_root=out_root, base_dir=tmp_path / "cases",
        args=_args(dry_run=False), index_row={"status": "success", "seq_cells": "0"})

    assert res["status"] == "promoted", res
    promoted = tmp_path / "cases" / "d1"
    text = (promoted / "rtl" / "top.v").read_text()
    assert str((promoted / "input" / "init.mem").resolve()) in text
    metadata = json.loads((promoted / "metadata.json").read_text())
    assert metadata["collateral_verified"] is True


def test_readmem_rebinding_is_scoped_to_the_consuming_rtl(tmp_path):
    rtl_a = tmp_path / "top_a.v"
    rtl_b = tmp_path / "top_b.v"
    body = 'module top; initial $readmemh("init.mem", mem); endmodule\n'
    rtl_a.write_text(body)
    rtl_b.write_text(body)
    payload_a = tmp_path / "a.mem"
    payload_b = tmp_path / "b.mem"
    payload_a.write_text("aa\n")
    payload_b.write_text("bb\n")

    rewrites = pc.rewrite_collateral_references(
        ["/source/a/top.v", "/source/b/top.v"],
        [rtl_a, rtl_b],
        {
            ("/source/a/top.v", "init.mem"): payload_a,
            ("/source/b/top.v", "init.mem"): payload_b,
        })

    assert len(rewrites) == 2
    assert str(payload_a.resolve()) in rtl_a.read_text()
    assert str(payload_b.resolve()) in rtl_b.read_text()
    assert str(payload_b.resolve()) not in rtl_a.read_text()


def test_headers_are_vendored_into_the_project(tmp_path):
    out_root, cdir = _corpus(tmp_path, headers={"defs.vh": "`define W 8\n"})
    hm = [{"path": "/nonexistent/home/other/defs.vh", "size": 12, "sha256": "x"}]

    rtl_dir = tmp_path / "proj" / "rtl"
    vendored, unresolved = pc.vendor_headers(hm, cdir, rtl_dir)
    assert unresolved == []
    # EXACT basename preserved — an `include resolves by name, so the
    # uniquifying rename vendor_rtl applies would break it.
    assert [p.name for p in vendored] == ["defs.vh"]
    assert (rtl_dir / "defs.vh").read_text() == "`define W 8\n"


def test_unvendorable_header_blocks_promotion(tmp_path):
    man = dict(_MAN, top_parameters={},
               header_manifest=[{"path": "/nonexistent/missing.vh",
                                 "size": 1, "sha256": "x"}])
    out_root, cdir = _corpus(tmp_path, compile_manifest=man)

    res = pc.promote_one("d1", out_root=out_root, base_dir=tmp_path / "cases",
                         args=_args(dry_run=False),
                         index_row={"status": "success", "seq_cells": "0"})
    assert res["status"] == "header_closure_unresolved", res


def test_colliding_header_basenames_are_reported_not_silently_merged(tmp_path):
    out_root, cdir = _corpus(tmp_path)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "defs.vh").write_text("`define W 8\n")
    (b / "defs.vh").write_text("`define W 16\n")     # SAME name, different bytes

    vendored, unresolved = pc.vendor_headers(
        [{"path": str(a / "defs.vh")}, {"path": str(b / "defs.vh")}],
        cdir, tmp_path / "proj" / "rtl")
    assert len(vendored) == 1
    assert any("collision" in u for u in unresolved)
