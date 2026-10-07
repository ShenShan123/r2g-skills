"""Guards on the sky130hd KLayout LVS deck and its equivalence generator.

ORFS's bundled `sky130hd.lylvs` is a Nangate deck with sky130 layer numbers
patched in, and r2g's deck was derived from it. Three defects came with that
inheritance, each of which made LVS report `Netlists don't match` on layouts
that are correct (2026-10-07, sky130hd corpus):

  substrate      `SUB = polygons(236, 0)` reads a layer sky130 standard cells
                 never draw, so SUB was EMPTY. Every nfet took its bulk from it,
                 landing on a global net with no path to ground, and the
                 deck's own `connect(SUB, PTAP)` had nothing to connect.
                 Extraction then saw substrate and ground as two nets where the
                 schematic ties each cell's VNB pin to VSS.

  device-less    FILL/TAP/CONB cells extract to no layout circuit but appear as
  cells          schematic subcircuit instances, which left the TOP cell's
                 comparison at status Skipped -- the real netlists were never
                 compared. Detection must be by property (no devices, no
                 subcircuits), not by a name list: a first pass matched
                 FILL|TAP|DECAP|DIODE|ANTENNA and missed CONB_1, a constant
                 cell with no transistors.

  equivalences   All 40 `equivalent_pins` entries named NANGATE cells
                 ("*AND2_1"), so no sky130 input was ever declared swappable.
                 Replacements must be UPPERCASE: KLayout's CDL reader upcases
                 every name, so lowercase patterns taken from the liberty match
                 nothing.

Plus one omission: the deck never called `report_lvs`, so `report_file` -- which
the Makefile passes -- was ignored and a mismatch left no database to inspect.
Four designs were investigated on the verdict line alone before this was added.

These tests do not run KLayout; they guard the deck's content, so that
re-syncing it from ORFS cannot silently reintroduce any of the above.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
DECK = SKILL / "assets" / "platforms" / "sky130hd" / "lvs" / "sky130hd_r2g.lylvs"
GEN = SKILL / "scripts" / "flow" / "gen_sky130_equivalent_pins.py"


@pytest.fixture(scope="module")
def deck() -> str:
    """The deck with comments stripped.

    Checks must read what the deck DOES, not what it says about itself: the
    comment explaining the old `SUB = polygons(236, 0)` quotes that string, and
    the one about the Nangate block quotes "*AND2_1".
    """
    assert DECK.is_file(), f"deck missing at {DECK}"
    out = []
    for line in DECK.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_substrate_is_derived_not_read_from_an_undrawn_layer(deck):
    assert "SUB = polygons(236, 0)" not in deck, \
        "236/0 is not drawn by sky130 standard cells; SUB would be empty"
    assert re.search(r"^SUB = extent - NWELL$", deck, re.M), \
        "the substrate must be the area outside the n-well"


def test_the_ptap_connection_is_still_present(deck):
    # It was always there; it was useless only because SUB was empty. The
    # substrate fix depends on it, so a future edit must not drop it.
    assert re.search(r"^connect\(SUB,\s+PTAP\)$", deck, re.M)


def test_device_less_circuits_are_flattened_by_property(deck):
    assert "each_subcircuit" in deck and "each_device" in deck, \
        "device-less detection must count devices and subcircuits"
    assert "FILL|TAP|DECAP|DIODE|ANTENNA" not in deck, \
        "a name list misses CONB_1 and whatever the next such cell is called"
    assert "flatten_circuit" in deck
    # flatten_circuit destroys the Circuit, so the loop must go through names
    assert "circuit_by_name" in deck, \
        "flatten by name: holding the Circuit dies on the next iteration"


def test_both_netlists_are_simplified_and_combined(deck):
    for call in ("netlist.simplify", "schematic.simplify",
                 "netlist.combine_devices", "schematic.combine_devices"):
        assert re.search(rf"^{re.escape(call)}$", deck, re.M), f"missing {call}"


def test_the_lvs_database_is_always_written(deck):
    assert "report_lvs($report_file)" in deck, \
        "without this, report_file is ignored and a mismatch leaves no database"


def test_the_equivalences_are_sky130s_and_not_the_inherited_nangate_block(deck):
    # Name alone cannot tell the two decks apart: sky130's and2_1 upcases to
    # AND2_1, which is also Nangate's name for that gate. What distinguishes
    # them is the cells only sky130 has, and the count -- the inherited block
    # was exactly 40 entries covering the simple gates alone.
    eq = [l for l in deck.splitlines() if "equivalent_pins(" in l]
    assert len(eq) > 100, \
        f"only {len(eq)} equivalences: looks like the 40-entry Nangate block"
    for sky130_only in ("A21OI_2", "O21AI_1", "A22OI_1"):
        assert any(f'"*{sky130_only}"' in l for l in eq), \
            f"{sky130_only} has no equivalence; Nangate has no such cell"


def test_equivalences_are_uppercase_and_sky130_shaped(deck):
    eq = [l for l in deck.splitlines() if "equivalent_pins(" in l]
    lower = [l for l in eq if re.search(r'"\*[a-z]', l)]
    assert not lower, \
        f"the CDL reader upcases names, so these match nothing: {lower[:3]}"
    # the cells that drove this: a21oi declares A1/A2, never B1 alongside them
    a21 = [l for l in eq if "A21OI_2" in l]
    assert a21, "A21OI_2 has no equivalence declared"
    assert all('"A1", "A2")' in l for l in a21), \
        f"A1/A2 are the AND-stage pair; B1 does not swap with them: {a21}"


def test_a_complex_gate_never_gets_all_inputs_declared_equivalent(deck):
    # Declaring B1 interchangeable with A1/A2 would make LVS accept layouts
    # that are wrong -- worse than the mismatch it would silence.
    for line in deck.splitlines():
        if "equivalent_pins(" not in line:
            continue
        # Only digit-suffixed pins denote groups (A1/A2 vs B1). A simple
        # gate's bare A, B, C, D are a single commutative set.
        args = re.findall(r'"([A-D]\d+)"', line)
        letters = {a[0] for a in args}
        assert len(letters) <= 1, \
            f"equivalence spans more than one input group: {line.strip()}"


# --------------------------------------------------------------- the generator

LIBERTY_FIXTURE = """\
library (fixture) {
  cell ("sky130_fd_sc_hd__nand2_1") {
    pin ("A") {
      direction : input;
    }
    pin ("B") {
      direction : input;
    }
    pin ("Y") {
      direction : output;
    }
    pin ("VPWR") {
      direction : input;
    }
  }
  cell ("sky130_fd_sc_hd__a21oi_2") {
    pin ("A1") {
      direction : input;
    }
    pin ("A2") {
      direction : input;
    }
    pin ("B1") {
      direction : input;
    }
    pin ("Y") {
      direction : output;
    }
  }
  cell ("sky130_fd_sc_hd__a22oi_1") {
    pin ("A1") {
      direction : input;
    }
    pin ("A2") {
      direction : input;
    }
    pin ("B1") {
      direction : input;
    }
    pin ("B2") {
      direction : input;
    }
    pin ("Y") {
      direction : output;
    }
  }
  cell ("sky130_fd_sc_hd__inv_1") {
    pin ("A") {
      direction : input;
    }
    pin ("Y") {
      direction : output;
    }
  }
}
"""


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> list[str]:
    # One attribute per line: the parser anchors on `re.match`, so a crammed pin
    # loses its direction and the test would pass vacuously.
    lib = tmp_path_factory.mktemp("lib") / "fixture.lib"
    lib.write_text(LIBERTY_FIXTURE)
    r = subprocess.run([sys.executable, str(GEN), str(lib)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return [l for l in r.stdout.splitlines() if l.strip()]


def test_generator_emits_uppercase_patterns(generated):
    assert generated, "generator produced nothing"
    for line in generated:
        m = re.search(r'equivalent_pins\("\*([^"]+)"', line)
        assert m, line
        assert m.group(1) == m.group(1).upper(), f"lowercase pattern: {line}"


def test_generator_covers_a_simple_commutative_gate(generated):
    assert any('"*NAND2_1", "A", "B")' in l for l in generated)


def test_generator_splits_complex_gates_into_groups(generated):
    a21 = [l for l in generated if "A21OI_2" in l]
    assert a21 == ['equivalent_pins("*A21OI_2", "A1", "A2")'], a21
    a22 = sorted(l for l in generated if "A22OI_1" in l)
    assert a22 == ['equivalent_pins("*A22OI_1", "A1", "A2")',
                   'equivalent_pins("*A22OI_1", "B1", "B2")'], a22


def test_generator_declares_nothing_for_a_single_input_cell(generated):
    assert not any("INV_1" in l for l in generated), \
        "an inverter has no commutative pair"


def test_generator_never_mixes_input_groups(generated):
    for line in generated:
        # As above: bare A/B on a simple gate are one group, A1/A2 vs B1 are not.
        args = re.findall(r'"([A-D]\d+)"', line)
        if not args:
            continue
        letters = {a[0] for a in args}
        assert len(letters) == 1, f"mixed groups: {line}"
