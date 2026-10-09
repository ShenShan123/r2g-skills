"""The CONTACT.3 tiling patch must not change what the rule reports.

`cont.not(active.or(poly.or(metal1)))` unions three layers and subtracts the
result from the contacts. After routing metal1 covers the die, so under `deep`
the union degenerates and the hierarchical engine falls back to flat: on a
6,563-cell design the stock deck reached 21 of 132 rules in 938s and was cut
off, while the tiled form finished all 133 in 257s. Those 111 unreached rules
were hiding 19 real antenna violations, so the timeout was not a slow check --
it was a blind one.

Tiling is only legitimate if the verdict is identical, and corpus layouts
cannot show that: they report zero CONTACT.3 violations, so patched and stock
agree trivially. The fixture forces the rule to fire, and the cases are chosen
to catch how a rewrite can go wrong -- in particular C5, a contact half over
metal1, whose exposed half must still be reported (an earlier
`interacting()`-based attempt was equivalent here but no faster, and a
per-polygon `not_inside` selection would report the whole contact instead).

Skips unless KLayout and an ORFS nangate45 platform are present, so the suite
stays runnable on a machine without the toolchain.
"""
from __future__ import annotations

import collections
import os
import pathlib
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "nangate45_contact3.gds"
BUNDLED = (HERE.parent / "assets" / "platforms" / "nangate45" / "drc"
           / "FreePDK45.lydrc")

RULE = ('cont.not(active.or(poly.or(metal1))).output("CONTACT.3", '
        '"CONTACT.3 : contact must be inside active or poly or metal1")')


def _klayout() -> str | None:
    return os.environ.get("KLAYOUT_CMD") or shutil.which("klayout")


def _run_deck(klayout: str, deck: pathlib.Path, gds: pathlib.Path,
              out: pathlib.Path) -> collections.Counter:
    """Return {category: {geometry, ...}} for one deck over one layout."""
    subprocess.run(
        [klayout, "-zz", "-rd", f"in_gds={gds}", "-rd", f"report_file={out}",
         "-r", str(deck)],
        capture_output=True, text=True, timeout=600, check=False)
    assert out.is_file(), f"{deck.name} 没有产出 lyrdb"
    found: dict[str, set[str]] = collections.defaultdict(set)
    for item in ET.parse(out).getroot().iter("item"):
        cat = item.find("category")
        name = (cat.text or "").strip().strip("'\"") if cat is not None else ""
        if not name:
            continue
        geo = next((((v.text or "").strip()) for v in item.iter("value")), "")
        found[name].add(geo)
    return found


@pytest.mark.skipif(_klayout() is None, reason="KLayout 不可用")
def test_tiled_contact3_matches_untiled(tmp_path: pathlib.Path) -> None:
    klayout = _klayout()
    assert FIXTURE.is_file(), f"缺少 fixture {FIXTURE}"

    # Reconstruct the pre-patch deck by stripping the tiling the patch added,
    # so the comparison has a reference even though upstream's copy is not in
    # the tree. Asserting the text changed guards against a no-op edit
    # silently making both arms identical.
    patched = BUNDLED.read_text(encoding="utf-8")
    assert "tiles(200.um)" in patched, "补丁不在 deck 里"
    stock = patched.replace("tiles(200.um)\ntile_borders(2.um)\n" + RULE
                            + "\ndeep", RULE)
    assert stock != patched, "剥离补丁后内容没变，两臂会是同一个 deck"
    stock_deck = tmp_path / "stock.lydrc"
    stock_deck.write_text(stock, encoding="utf-8")

    got_stock = _run_deck(klayout, stock_deck, FIXTURE, tmp_path / "s.lyrdb")
    got_tiled = _run_deck(klayout, BUNDLED, FIXTURE, tmp_path / "t.lyrdb")

    # The fixture exists to make the rule fire; if it does not, the comparison
    # below is vacuous and the test would pass without testing anything.
    assert got_stock.get("CONTACT.3"), "fixture 没有触发 CONTACT.3，比对无意义"

    assert set(got_tiled) == set(got_stock), (
        f"类别不同: 仅 tiled {set(got_tiled) - set(got_stock)}, "
        f"仅 stock {set(got_stock) - set(got_tiled)}")
    for cat in sorted(got_stock):
        assert got_tiled[cat] == got_stock[cat], (
            f"{cat} 几何不同: tiled 漏报 {got_stock[cat] - got_tiled[cat]}, "
            f"多报 {got_tiled[cat] - got_stock[cat]}")

    # C4 sits on bare substrate and C5 half over metal1, so exactly two
    # violations, and C5's must be the exposed half rather than the whole
    # contact -- the distinction a per-polygon rewrite would lose.
    c3 = got_stock["CONTACT.3"]
    assert len(c3) == 2, f"预期 2 条 CONTACT.3，得到 {len(c3)}: {c3}"
    assert any("16.85" in g for g in c3), (
        f"C5 应只报露出的那半（x 从 16.85 起），实际 {c3}")
