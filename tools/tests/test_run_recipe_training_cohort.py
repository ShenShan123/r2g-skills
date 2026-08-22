import importlib.util
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_recipe_training_cohort.py"
SPEC = importlib.util.spec_from_file_location("recipe_training_cohort", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_source_identity_recovers_expander_repository_and_commit(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top(input clk); always @(posedge clk); endmodule\n")

    source, url, resolved_commit = MODULE.source_identity(rtl)

    assert source == rtl.parents[1]
    assert url == "https://github.com/acme/core.git"
    assert resolved_commit == commit


def test_row_inputs_rejects_files_from_two_commits(tmp_path):
    files = []
    for commit in ("a" * 40, "b" * 40):
        rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
        rtl.parent.mkdir(parents=True)
        rtl.write_text("module top; endmodule\n")
        files.append(rtl)

    try:
        MODULE.row_inputs({"rtl_files": ";".join(map(str, files))})
    except ValueError as exc:
        assert "unbound" in str(exc)
    else:
        raise AssertionError("cross-commit compilation closure was accepted")


def test_row_inputs_adds_transitive_headers_and_readmem_payload(tmp_path):
    commit = "a" * 40
    source = tmp_path / "repositories/github/acme/core" / commit / "source"
    rtl = source / "rtl/top.sv"
    header = source / "rtl/include/params.svh"
    payload = source / "rtl/data/weights.hex"
    rtl.parent.mkdir(parents=True)
    header.parent.mkdir(parents=True)
    payload.parent.mkdir(parents=True)
    rtl.write_text('`include "include/params.svh"\nmodule top; endmodule\n')
    header.write_text('$readmemh("../data/weights.hex", memory);\n')
    payload.write_text("00\n")

    _, closure = MODULE.row_inputs(
        {"rtl_files": str(rtl), "include_dirs": str(source / "rtl")}
    )

    assert {path.resolve() for path in closure} == {
        rtl.resolve(),
        header.resolve(),
        payload.resolve(),
    }


def test_row_inputs_rejects_dynamic_readmem_payload(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top; initial $readmemh(memory_path, memory); endmodule\n")

    try:
        MODULE.row_inputs({"rtl_files": str(rtl), "include_dirs": str(rtl.parent)})
    except ValueError as exc:
        assert "unresolved compilation collateral" in str(exc)
    else:
        raise AssertionError("dynamic readmem payload was accepted")


def test_row_inputs_rejects_missing_include(tmp_path):
    commit = "a" * 40
    rtl = tmp_path / "repositories/github/acme/core" / commit / "source/rtl/top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text('`include "missing.svh"\nmodule top; endmodule\n')

    try:
        MODULE.row_inputs({"rtl_files": str(rtl), "include_dirs": str(rtl.parent)})
    except ValueError as exc:
        assert "unresolved include closure" in str(exc)
    else:
        raise AssertionError("missing include was accepted")


def test_family_id_is_read_from_expander_notes():
    assert MODULE.family_id({"design": "d1", "notes": "design_id=d1; family_id=f123; release=x"}) == "f123"


def test_write_json_is_atomic_and_read_json_round_trips(tmp_path):
    path = tmp_path / "state/result.json"
    MODULE.write_json(path, {"strict_clean": False, "signature": ["DRC:mcon.5"]})

    assert MODULE.read_json(path) == {
        "strict_clean": False,
        "signature": ["DRC:mcon.5"],
    }


def test_stable_replay_projects_requires_all_eligible_results(tmp_path):
    campaign = tmp_path / "campaign"
    for name in ("a", "b"):
        MODULE.write_json(campaign / f"state/failure_replay/{name}/attempt_1.json", {})
    MODULE.write_json(
        campaign / "state/failure_replay_summary.json",
        {"results": [{"project": "/p/a", "status": "stable_repair_challenge"}]},
    )

    complete, stable = MODULE.stable_replay_projects(campaign)

    assert complete is False
    assert stable == [Path("/p/a")]


def test_stable_replay_projects_filters_unstable_results(tmp_path):
    campaign = tmp_path / "campaign"
    for name in ("a", "b"):
        MODULE.write_json(campaign / f"state/failure_replay/{name}/attempt_1.json", {})
    MODULE.write_json(
        campaign / "state/failure_replay_summary.json",
        {
            "results": [
                {"project": "/p/a", "status": "stable_repair_challenge"},
                {"project": "/p/b", "status": "unstable_or_ineligible"},
            ]
        },
    )

    complete, stable = MODULE.stable_replay_projects(campaign)

    assert complete is True
    assert stable == [Path("/p/a")]


def test_quarantine_preexisting_candidates_is_platform_scoped(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE recipe_status ("
        "symptom_id TEXT, design_class TEXT, platform TEXT, strategy TEXT, "
        "status TEXT, provenance TEXT, generation INTEGER, updated_at TEXT, "
        "status_version INTEGER)"
    )
    conn.executemany(
        "INSERT INTO recipe_status VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("s1", "logic/small", "sky130hd", "density_relief", "candidate", "old", 1, "t0", 2),
            ("s2", "logic/small", "sky130hd", "setup_slack_margin", "promoted", "ab", 1, "t0", 3),
            ("s3", "logic/small", "nangate45", "period_relax", "candidate", "old", 1, "t0", 4),
        ],
    )
    conn.commit()
    conn.close()

    records = MODULE.quarantine_preexisting_candidates(db, "sky130hd")

    assert [(row["symptom_id"], row["strategy"]) for row in records] == [
        ("s1", "density_relief")
    ]
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT symptom_id, status, provenance, status_version FROM recipe_status "
        "ORDER BY symptom_id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("s1", "parked", "campaign_quarantine_preexisting_candidate", 3),
        ("s2", "promoted", "ab", 3),
        ("s3", "candidate", "old", 4),
    ]


def test_fixed_clock_target_excludes_period_relax_without_losing_other_exclusions():
    original = {"R2G_FIX_EXCLUDE": "unsafe_recipe", "KEEP": "yes"}

    protected = MODULE.protect_fixed_clock_target(original)

    assert protected["R2G_FIX_EXCLUDE"] == "unsafe_recipe,period_relax"
    assert protected["KEEP"] == "yes"
    assert original["R2G_FIX_EXCLUDE"] == "unsafe_recipe"
    assert MODULE.protect_fixed_clock_target(protected)["R2G_FIX_EXCLUDE"] == (
        "unsafe_recipe,period_relax"
    )
