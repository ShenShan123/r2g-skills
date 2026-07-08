"""Unit tests for the platform-parameterized feature paths (off-nangate45 behavior)."""
from __future__ import annotations

import textwrap

# Production source of truth: the consolidated techlib package (the feature workers
# import these in Tasks 7/8). cell_type_map / def_parse are now aliases onto techlib —
# the old features/{cell_type_map,def_parse}.py copies were deleted in Task 9.
from techlib import cell_types as cell_type_map
from techlib import def_parse
# Routing-layer NAMES + regex are canonically homed in techlib.lef (nodes_net consumes
# them from there); techlib.def_parse keeps verbatim copies, but match production.
from techlib import lef


# --- cell-type map ---------------------------------------------------------

def test_nangate45_uses_runtime_map():
    # Curated map retired 2026-07-06 — nangate45 builds from liberty like everyone else.
    lib_db = {"cells": {"INV_X1": {"source_lib": "s"}, "SDFF_X1": {"source_lib": "s"}}}
    mp = cell_type_map.resolve_cell_type_map("nangate45", lib_db)
    assert mp == {"INV_X1": 0, "SDFF_X1": 1, "UNKNOWN": 2, "MACRO": 3}
    assert cell_type_map.cell_type_id("sdff_x1", mp) == 1  # drifted master heals


def test_other_platform_builds_deterministic_runtime_map():
    lib_db = {"cells": {"SKY130_FD_SC_HD__NAND2_1": {}, "SKY130_FD_SC_HD__INV_1": {}}}
    mp = cell_type_map.resolve_cell_type_map("sky130hd", lib_db)
    # sorted cell names -> 0..N-1 (INV_1 < NAND2_1), UNKNOWN=N, MACRO=N+1; stable across calls.
    assert mp == {"SKY130_FD_SC_HD__INV_1": 0, "SKY130_FD_SC_HD__NAND2_1": 1,
                  "UNKNOWN": 2, "MACRO": 3}
    assert cell_type_map.cell_type_id("sky130_fd_sc_hd__inv_1", mp) == 0
    assert cell_type_map.cell_type_id("not_a_cell", mp) == 2  # UNKNOWN


def test_runtime_map_is_stable_across_designs_via_std_cell_filter():
    # The cell-type id space must be the platform's std-cell vocabulary, NOT the
    # per-design (std + macro) resolved set — else a macro lib reshuffles std-cell ids
    # across designs of the same platform (review finding #8).
    std = "/p/std.lib"
    macro = "/p/macro.lib"
    # "AMACRO_64X32" sorts before the std cells, so it WOULD shift their ids if included.
    with_macro = {"cells": {
        "INV_1": {"source_lib": std}, "NAND2_1": {"source_lib": std},
        "AMACRO_64X32": {"source_lib": macro},
    }}
    no_macro = {"cells": {"INV_1": {"source_lib": std}, "NAND2_1": {"source_lib": std}}}
    mp1 = cell_type_map.build_runtime_map(with_macro, [std])
    mp2 = cell_type_map.build_runtime_map(no_macro, [std])
    # std-cell ids identical whether or not a macro lib was loaded
    assert mp1["INV_1"] == mp2["INV_1"] == 0
    assert mp1["NAND2_1"] == mp2["NAND2_1"] == 1
    # macro cell is excluded from the std id space -> shared MACRO id (N+1), NOT
    # UNKNOWN (N) — macros are known nodes, distinguishable from unmapped masters
    # (2026-07-06 nangate45 fakeram audit).
    assert cell_type_map.cell_type_id("amacro_64x32", mp1) == mp1["MACRO"]
    assert mp1["MACRO"] == mp1["UNKNOWN"] + 1
    assert cell_type_map.cell_type_id("not_a_cell_at_all", mp1) == mp1["UNKNOWN"]
    # without the std-cell filter, the macro WOULD shift std-cell ids (the bug #8 prevents)
    unfiltered = cell_type_map.build_runtime_map(with_macro)
    assert unfiltered["NAND2_1"] != mp1["NAND2_1"]  # AMACRO_64X32 sorts before, shifts +1


# --- tech-LEF routing-layer matcher ----------------------------------------

NANGATE_LEF = textwrap.dedent("""
    LAYER metal1
      TYPE ROUTING ;
    END metal1
    LAYER via1
      TYPE CUT ;
    END via1
    LAYER metal10
      TYPE ROUTING ;
    END metal10
""")

SKY130_LEF = textwrap.dedent("""
    LAYER li1
      TYPE ROUTING ;
    END li1
    LAYER mcon
      TYPE CUT ;
    END mcon
    LAYER met1
      TYPE ROUTING ;
    END met1
""")


def test_routing_layers_exclude_cut_layers(tmp_path):
    p = tmp_path / "n.lef"
    p.write_text(NANGATE_LEF)
    assert lef.routing_layers(str(p)) == ["metal1", "metal10"]
    p2 = tmp_path / "s.lef"
    p2.write_text(SKY130_LEF)
    assert lef.routing_layers(str(p2)) == ["li1", "met1"]


def test_layer_regex_full_token_match(tmp_path):
    p = tmp_path / "n.lef"
    p.write_text(NANGATE_LEF)
    rx, from_lef = lef.routing_layer_regex(str(p))
    assert from_lef is True
    # metal1 must not match inside metal10 (word boundary + longest-first).
    assert rx.search("+ ROUTED metal10 ( 0 0 )").group(1) == "metal10"
    assert rx.search("+ ROUTED metal1 ( 0 0 )").group(1) == "metal1"
    assert rx.search("+ ROUTED via1 ( 0 0 )") is None


def test_layer_regex_fallback_when_no_lef():
    rx, from_lef = lef.routing_layer_regex("")
    assert from_lef is False
    assert rx.search("ROUTED metal7 ( 0 0 )").group(1) == "metal7"


# --- shared DEF parsers ----------------------------------------------------

TINY_DEF = textwrap.dedent("""
    DESIGN tiny ;
    UNITS DISTANCE MICRONS 1000 ;
    COMPONENTS 2 ;
    - i1 INV_X1 + PLACED ( 1000 2000 ) N ;
    - i2 NAND2_X1 + FIXED ( 3000 4000 ) FS ;
    END COMPONENTS
    NETS 1 ;
    - n1 ( i1 ZN ) ( i2 A1 ) ( PIN clk )
      + ROUTED metal1 ( 0 0 ) ( 1000 0 )
      + USE SIGNAL ;
    END NETS
    END DESIGN
""")


def test_parse_components_preserves_order_status_orient(tmp_path):
    p = tmp_path / "t.def"
    p.write_text(TINY_DEF)
    comps = def_parse.parse_components(str(p))
    assert list(comps.keys()) == ["i1", "i2"]  # DEF declaration order
    assert comps["i1"] == {"master": "INV_X1", "status": "PLACED", "orient": "N", "x": 1000, "y": 2000}
    assert comps["i2"]["status"] == "FIXED" and comps["i2"]["orient"] == "FS"


def test_parse_nets_filters_coord_pairs_and_keeps_conns(tmp_path):
    p = tmp_path / "t.def"
    p.write_text(TINY_DEF)
    nets = def_parse.parse_nets(str(p))
    assert list(nets["n1"]["conns"]) == [("i1", "ZN"), ("i2", "A1"), ("PIN", "clk")]
    assert nets["n1"]["use"] == "SIGNAL"
    assert any("ROUTED metal1" in r for r in nets["n1"]["routes"])


WRAP_DEF = textwrap.dedent("""
    DESIGN tiny ;
    UNITS DISTANCE MICRONS 1000 ;
    COMPONENTS 0 ;
    END COMPONENTS
    NETS 1 ;
    - n1 ( i1 Z )
      ( u_ROUTED_pkt A ) ( i3 B )
      + ROUTED metal1 ( 0 0 ) ( 100 0 )
        NEW metal2 ( 100 0 ) ( 100 100 ) ;
    END NETS
    END DESIGN
""")


def test_parse_nets_does_not_drop_conn_lines_containing_substring_routed(tmp_path):
    # A wrapped connection line whose instance name contains "ROUTED" must NOT be
    # diverted to routes (review finding #1) — the routes branch keys on a leading
    # keyword, not a substring.
    p = tmp_path / "t.def"
    p.write_text(WRAP_DEF)
    nets = def_parse.parse_nets(str(p))
    assert ("u_ROUTED_pkt", "A") in nets["n1"]["conns"]
    assert nets["n1"]["conns"] == [("i1", "Z"), ("u_ROUTED_pkt", "A"), ("i3", "B")]
    # routing layers still captured for num_layer
    rx, _ = lef.routing_layer_regex("")
    layers = {rx.search(r).group(1).lower() for r in nets["n1"]["routes"] if rx.search(r)}
    assert layers == {"metal1", "metal2"}
