"""Unit tests for techlib.spef — the shared SPEF parser feeding RC labels.

Ground-truthed on real ORFS SPEFs (nangate45 gcd + sky130hd apb_master, 2026-07-07);
these pin the numeric behavior on a hand-computable synthetic SPEF so a regression in
unit scaling / name resolution / equivalent-resistance reduction is caught fast.
"""
import os
import sys

_EXTRACT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "extract")
if _EXTRACT not in sys.path:
    sys.path.insert(0, _EXTRACT)

from techlib import spef  # noqa: E402

# Synthetic SPEF: C_UNIT PF (x1000 fF), R_UNIT OHM.
#   net n0 (*1): pins g0/ZN(drv,O), g1/A(sink,I), port in0.
#     ground = (0.001+0.001)*1000 = 2.0 fF ; coupling to n1 via *2:5 = 0.0005*1000 = 0.5 fF
#     RES tree: ZN-mid 10, mid-A 20, mid-in0 5  ->  R(ZN,A)=30, R(ZN,in0)=15, R(A,in0)=25
#   net n1 (*2): pins g1/ZN(drv), g0/A(sink); ground=(0.0015+0.0015)*1000=3.0; R(g1/ZN,g0/A)=30
SYNTH = """*SPEF "ieee 1481-1999"
*DESIGN "t"
*C_UNIT 1 PF
*R_UNIT 1 OHM
*NAME_MAP
*1 n0
*2 n1
*10 g0
*11 g1
*PORTS
in0 I
*D_NET *1 0.005
*CONN
*I *10:ZN O *D INV
*I *11:A I *D INV
*P in0 I
*CAP
1 *10:ZN 0.001
2 *11:A 0.001
3 *11:A *2:5 0.0005
*RES
1 *10:ZN *10:mid 10
2 *10:mid *11:A 20
3 *10:mid in0 5
*END
*D_NET *2 0.003
*CONN
*I *11:ZN O *D INV
*I *10:A I *D INV
*CAP
1 *11:ZN 0.0015
2 *10:A 0.0015
*RES
1 *11:ZN *10:A 30
*END
"""


def _write(tmp_path):
    p = tmp_path / "t.spef"
    p.write_text(SYNTH)
    return str(p)


def test_units_and_names(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    assert d is not None
    assert d.cap_scale_ff == 1000.0  # PF -> fF
    assert d.res_scale_ohm == 1.0
    assert d.id2name["*1"] == "n0" and d.id2name["*10"] == "g0"
    assert set(d.nets) == {"n0", "n1"}


def test_ground_cap(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    assert d.net_ground_cap_ff["n0"] == 2.0
    assert d.net_ground_cap_ff["n1"] == 3.0
    # header cap parsed too
    assert abs(d.net_total_cap_ff["n0"] - 5.0) < 1e-9


def test_coupling_cross_net_only_and_keyed_sorted(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    assert list(d.coupling_cap_ff.keys()) == [("n0", "n1")]  # sorted, cross-net, once
    assert abs(d.coupling_cap_ff[("n0", "n1")] - 0.5) < 1e-9


# write_spef emits each coupling cap SYMMETRICALLY — once in each participating
# net's *CAP block. The parser must count each physical capacitor ONCE regardless
# of the node order used in the mirror (2026-07-07 review: guards against a ~2x
# double-count). Three cross-net coupling caps between n0/n1 (pin-pin, internal-pin,
# pin-internal), each mirrored in the OTHER block with SWAPPED node order.
# n0 pins: g0/ZN (net n0); n1 pins: g1/A (net n1). Correct total = 1+2+3 = 6.0 fF.
SYMM = """*SPEF "ieee 1481-1999"
*C_UNIT 1 FF
*R_UNIT 1 OHM
*NAME_MAP
*1 n0
*2 n1
*10 g0
*11 g1
*D_NET *1 0
*CONN
*I *10:ZN O *D INV
*CAP
1 *10:ZN 5.0
2 *10:ZN *11:A 1.0
3 *1:5 *11:A 2.0
4 *10:ZN *2:7 3.0
*END
*D_NET *2 0
*CONN
*I *11:A I *D INV
*CAP
5 *11:A 6.0
6 *11:A *10:ZN 1.0
7 *11:A *1:5 2.0
8 *2:7 *10:ZN 3.0
*END
"""


def test_coupling_symmetric_writer_counts_once(tmp_path):
    # each coupling cap is mirrored (swapped order) in the other block; the parser
    # must total each physical cap once -> 1+2+3 = 6.0 fF (not 12.0).
    p = tmp_path / "sym.spef"
    p.write_text(SYMM)
    d = spef.parse_spef(str(p))
    assert list(d.coupling_cap_ff.keys()) == [("n0", "n1")]
    assert abs(d.coupling_cap_ff[("n0", "n1")] - 6.0) < 1e-9, d.coupling_cap_ff
    # ground cap is the grounded (2-arg) entries only, per block (not deduped)
    assert abs(d.net_ground_cap_ff["n0"] - 5.0) < 1e-9
    assert abs(d.net_ground_cap_ff["n1"] - 6.0) < 1e-9


def test_driver_is_output_pin(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    assert d.net_driver["n0"] == ("g0", "ZN")
    assert d.net_driver["n1"] == ("g1", "ZN")


def test_equiv_res_tree_reduction(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    pairs = {(a, b): round(r, 6) for a, b, r in d.equiv_res_pairs("n0")}
    # keys are sorted pinkeys; port encoded ("PIN","in0")
    assert pairs[(("PIN", "in0"), ("g0", "ZN"))] == 15.0
    assert pairs[(("PIN", "in0"), ("g1", "A"))] == 25.0
    assert pairs[(("g0", "ZN"), ("g1", "A"))] == 30.0
    n1_pairs = {(a, b): round(r, 6) for a, b, r in d.equiv_res_pairs("n1")}
    assert n1_pairs[(("g0", "A"), ("g1", "ZN"))] == 30.0


def test_max_fanout_guard(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    # n0 has 3 pins; cap at 2 -> skipped marker
    res = d.equiv_res_pairs("n0", max_fanout=2)
    assert isinstance(res, dict) and res.get("skipped") == 3


def test_total_cap_helper(tmp_path):
    d = spef.parse_spef(_write(tmp_path))
    assert abs(spef.total_cap_ff(d) - 8.0) < 1e-9  # 5.0 + 3.0


def test_missing_file_returns_none():
    assert spef.parse_spef("/no/such/file.spef") is None
    assert spef.parse_spef("") is None


def test_deesc_matches_def_convention():
    # SPEF escapes . $ : etc; DEF (def_parse) escapes only bus [ ]. de-escaping
    # must strip backslash EXCEPT before [ ] so names join the DEF feature CSVs.
    assert spef._deesc(r"dec_block\.block_w0_reg\[0\]") == r"dec_block.block_w0_reg\[0\]"
    assert spef._deesc(r"keymem\.key_mem\[12\]\[54\]\$_DFFE_PN0P_") == r"keymem.key_mem\[12\]\[54\]$_DFFE_PN0P_"
    assert spef._deesc("plain_name") == "plain_name"   # no-op fast path
    # a literal '/' inside a name is escaped in BOTH SPEF and DEF ('\/'); it must stay
    # escaped or the name no longer joins the DEF CSVs (failure-patterns #66, 2026-11-07).
    assert spef._deesc(r"a\/b") == r"a\/b"


def test_literal_slash_net_joins_def_name(tmp_path):
    """Regression (pilot500 C00180): yosys names like `AddRoundKey$func$/path/x.v:193$1.ark.u[0]`
    are written by write_def as `AddRoundKey$func$\/path\/x.v:193$1.ark.u\[0\]` and by
    write_spef as `AddRoundKey\$func\$\/path\/x\.v\:193\$1\.ark\.u\[0\]`; the de-escaped SPEF name
    must equal the DEF one."""
    spef_name = r"AddRoundKey\$func\$\/home\/src\/x\.v\:193\$1\.ark\.u\[0\]"
    def_name = r"AddRoundKey$func$\/home\/src\/x.v:193$1.ark.u\[0\]"
    assert spef._deesc(spef_name) == def_name


def test_names_are_deescaped_end_to_end(tmp_path):
    # a net + inst with escaped '.' in the NAME_MAP must surface DEF-convention.
    p = tmp_path / "e.spef"
    p.write_text("*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*NAME_MAP\n"
                 r"*1 blk\.n0" "\n" r"*10 blk\.g0\[3\]" "\n"
                 "*D_NET *1 0\n*CONN\n*I *10:ZN O *D INV\n*CAP\n1 *10:ZN 0.001\n*END\n")
    d = spef.parse_spef(str(p))
    assert d.id2name["*1"] == "blk.n0"                 # '.' de-escaped
    assert d.id2name["*10"] == r"blk.g0\[3\]"          # '.' de-escaped, '[' kept
    assert "blk.n0" in d.net_ground_cap_ff
    assert d.net_driver["blk.n0"] == (r"blk.g0\[3\]", "ZN")


def test_pf_scaling_variants(tmp_path):
    # NF unit -> x1e6 ; KOHM -> x1e3
    p = tmp_path / "u.spef"
    p.write_text("*C_UNIT 1 NF\n*R_UNIT 2 KOHM\n*NAME_MAP\n*1 a\n"
                 "*D_NET *1 0\n*CONN\n*I *5:Y O *D X\n*CAP\n1 *5:Y 0.000001\n*END\n")
    d = spef.parse_spef(str(p))
    assert d.cap_scale_ff == 1e6
    assert d.res_scale_ohm == 2000.0
    assert abs(d.net_ground_cap_ff["a"] - 1.0) < 1e-9  # 1e-6 NF * 1e6 = 1.0 fF
