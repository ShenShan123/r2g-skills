"""Tests for extract_drc.py: true item-count vs inflated marker count."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "extract_drc.py"


def _run(proj_dir: Path, out_path: Path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(proj_dir), str(out_path)],
        capture_output=True, text=True,
    )
    return r


# Minimal lyrdb with 2 categories and 3 <item> elements.
# Each item has multiple <value> children so that <value>-count >> item count,
# mirroring the real nangate45 antenna lyrdb (7 <value> tags per item).
_LYRDB_3_ITEMS = """\
<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <description>Test DRC runset</description>
 <original-file/>
 <generator>test</generator>
 <top-cell>test_top</top-cell>
 <tags/>
 <categories>
  <category>
   <name>METAL4_ANTENNA</name>
   <description>METAL4_ANTENNA : antenna ratio exceeded</description>
   <categories/>
  </category>
  <category>
   <name>METAL7_ANTENNA</name>
   <description>METAL7_ANTENNA : antenna ratio exceeded</description>
   <categories/>
  </category>
 </categories>
 <items>
  <item>
   <tags/>
   <category>METAL4_ANTENNA</category>
   <cell>test_top</cell>
   <visited>false</visited>
   <multiplicity>1</multiplicity>
   <comment/>
   <image/>
   <values>
    <value>polygon: (10,20;10,80;20,80;20,20)</value>
    <value>[#ametal] float: 4.8</value>
    <value>[#agate] float: 0.025</value>
    <value>[#ratio] float: 192.0</value>
    <value>[#adiodes] text: '(0)'</value>
    <value>[#max_ratio] float: 300</value>
    <value>[#diode_factors] text: '(0)'</value>
   </values>
  </item>
  <item>
   <tags/>
   <category>METAL4_ANTENNA</category>
   <cell>test_top</cell>
   <visited>false</visited>
   <multiplicity>1</multiplicity>
   <comment/>
   <image/>
   <values>
    <value>polygon: (30,20;30,80;40,80;40,20)</value>
    <value>[#ametal] float: 4.8</value>
    <value>[#agate] float: 0.025</value>
    <value>[#ratio] float: 192.0</value>
    <value>[#adiodes] text: '(0)'</value>
    <value>[#max_ratio] float: 300</value>
    <value>[#diode_factors] text: '(0)'</value>
   </values>
  </item>
  <item>
   <tags/>
   <category>METAL7_ANTENNA</category>
   <cell>test_top</cell>
   <visited>false</visited>
   <multiplicity>1</multiplicity>
   <comment/>
   <image/>
   <values>
    <value>polygon: (50,0;50,100;60,100;60,0)</value>
    <value>[#ametal] float: 9.0</value>
    <value>[#agate] float: 0.025</value>
    <value>[#ratio] float: 360.0</value>
    <value>[#adiodes] text: '(0)'</value>
    <value>[#max_ratio] float: 300</value>
    <value>[#diode_factors] text: '(0)'</value>
   </values>
  </item>
 </items>
</report-database>
"""

# The inflated count.rpt value: 3 items × 7 <value> tags each = 21
_INFLATED_COUNT = 21


def _make_project(tmp_path: Path, *, lyrdb_content: str | None, count_rpt: int | None) -> Path:
    proj = tmp_path / "proj"
    drc_dir = proj / "drc"
    drc_dir.mkdir(parents=True)
    if lyrdb_content is not None:
        (drc_dir / "6_drc.lyrdb").write_text(lyrdb_content, encoding="utf-8")
    if count_rpt is not None:
        (drc_dir / "6_drc_count.rpt").write_text(str(count_rpt), encoding="utf-8")
    return proj


def test_true_item_count_preferred_over_inflated_marker_count(tmp_path):
    """total_violations == 3 (item count), raw_marker_count == 21 (value-tag count)."""
    proj = _make_project(tmp_path, lyrdb_content=_LYRDB_3_ITEMS, count_rpt=_INFLATED_COUNT)
    out = tmp_path / "drc.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())

    # True item count from parsed lyrdb
    assert result["total_violations"] == 3, f"expected 3 items, got {result['total_violations']}"

    # Inflated marker count preserved for transparency
    assert result["raw_marker_count"] == _INFLATED_COUNT, (
        f"expected raw_marker_count={_INFLATED_COUNT}, got {result['raw_marker_count']}"
    )

    # Categories sum to 3
    cats = result["categories"]
    cat_sum = sum(c["count"] for c in cats.values())
    assert cat_sum == 3, f"category sum expected 3, got {cat_sum}"

    # Status must reflect a non-zero violation count
    assert result["status"] == "fail"


def test_clean_design_total_zero_status_clean(tmp_path):
    """count.rpt=0, no lyrdb → total_violations=0, status=clean."""
    proj = _make_project(tmp_path, lyrdb_content=None, count_rpt=0)
    out = tmp_path / "drc_clean.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())

    assert result["total_violations"] == 0
    assert result["status"] == "clean"
    # No lyrdb → raw_marker_count is the count.rpt value
    assert result["raw_marker_count"] == 0


def test_drc_mode_beol_only_carried_through(tmp_path):
    """drc_result.json with drc_mode=beol_only is propagated into reports/drc.json."""
    proj = _make_project(tmp_path, lyrdb_content=_LYRDB_3_ITEMS, count_rpt=_INFLATED_COUNT)
    # Write a drc_result.json that mirrors what run_drc.sh emits in BEOL-only mode
    drc_result = {
        "status": "violations",
        "violations": 3,
        "drc_mode": "beol_only",
    }
    (proj / "drc" / "drc_result.json").write_text(
        json.dumps(drc_result), encoding="utf-8"
    )
    out = tmp_path / "drc_beol.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())

    # drc_mode must be carried through to the output
    assert result.get("drc_mode") == "beol_only", (
        f"expected drc_mode='beol_only', got {result.get('drc_mode')!r}"
    )
    # Status should reflect the lyrdb item count (3 violations)
    assert result["status"] == "fail"
    assert result["total_violations"] == 3


def test_drc_mode_beol_only_clean_is_qualified(tmp_path):
    """A 0-violation BEOL-only run must NOT report plain 'clean'.

    BEOL-only mode disables BOTH the FEOL and ANTENNA rule groups (see
    run_drc.sh / commit 56a1175), so a 0-violation result only proves the
    metal/via/cut routing is clean — it says nothing about FEOL geometry or
    antenna ratios.  Reporting it as full 'clean' would silently inflate the
    corpus clean-rate.  It must be the qualified status 'clean_beol' so that
    status-based aggregation cannot miscount it (mirrors LVS 'clean_algorithmic').
    """
    proj = _make_project(tmp_path, lyrdb_content=None, count_rpt=0)
    drc_result = {
        "status": "clean",
        "violations": 0,
        "drc_mode": "beol_only",
    }
    (proj / "drc" / "drc_result.json").write_text(
        json.dumps(drc_result), encoding="utf-8"
    )
    out = tmp_path / "drc_beol_clean.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())

    assert result["total_violations"] == 0
    assert result.get("drc_mode") == "beol_only"
    assert result["status"] == "clean_beol", (
        f"BEOL-only 0-violation must be 'clean_beol', got {result['status']!r}"
    )


def test_drc_mode_beol_strict_clean_is_qualified(tmp_path):
    """A 0-violation beol_only_strict run is also clean_beol (strips whole FEOL body)."""
    proj = _make_project(tmp_path, lyrdb_content=None, count_rpt=0)
    (proj / "drc" / "drc_result.json").write_text(
        json.dumps({"status": "clean", "violations": 0,
                    "drc_mode": "beol_only_strict"}), encoding="utf-8"
    )
    out = tmp_path / "drc_beol_nc.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())
    assert result.get("drc_mode") == "beol_only_strict"
    assert result["status"] == "clean_beol", (
        f"beol_only_strict 0-viol must be 'clean_beol', got {result['status']!r}"
    )


def test_drc_mode_full_carried_through(tmp_path):
    """drc_result.json with drc_mode=full is propagated into reports/drc.json."""
    proj = _make_project(tmp_path, lyrdb_content=None, count_rpt=0)
    drc_result = {
        "status": "clean",
        "violations": 0,
        "drc_mode": "full",
    }
    (proj / "drc" / "drc_result.json").write_text(
        json.dumps(drc_result), encoding="utf-8"
    )
    out = tmp_path / "drc_full.json"
    r = _run(proj, out)
    assert r.returncode == 0, r.stderr
    result = json.loads(out.read_text())

    assert result.get("drc_mode") == "full", (
        f"expected drc_mode='full', got {result.get('drc_mode')!r}"
    )
    assert result["status"] == "clean"
