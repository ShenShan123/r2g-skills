#!/usr/bin/env python3
"""Stdlib-only self-test of the ledger implementation.

The pytest-based tests in ``tests/`` are the canonical suite (and what the
autograder team should mirror). This script runs the *same* invariants with
no third-party deps, so the implementation can be smoke-tested anywhere
Python 3.10+ is available.

Run: ``python3 run_selftest.py``
Exit code 0 on full pass; non-zero on any failure.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

# Make scripts_ledger importable
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from append_ledger import (
    FORBIDDEN_TRIGGER, LEDGER_FILENAME, append_record,
)
from canonical import (
    canonical_json_bytes, compute_record_hash, verify_record_hash,
)
from metrics_parsers import (
    METRICS_PARSERS, get_parser,
    parse_drc_klayout, parse_label_wirelength, parse_lint, parse_lvs_klayout,
    parse_orfs_backend, parse_simulation, parse_synthesis, parse_timing_check,
)


# ─── tiny test harness ──────────────────────────────────────────────────────

class Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def run(self, name, fn):
        try:
            fn()
            self.passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            self.failed += 1
            self.failures.append(f"{name}: {e}")
            print(f"  FAIL  {name}: {e}")
        except Exception:
            self.failed += 1
            tb = traceback.format_exc()
            self.failures.append(f"{name}: unexpected exception\n{tb}")
            print(f"  ERR   {name}: see traceback below")
            print(tb)


def expect_raises(exc_type, fn, msg_contains=None):
    try:
        fn()
    except exc_type as e:
        if msg_contains and msg_contains not in str(e):
            raise AssertionError(
                f"raised {exc_type.__name__} but message {str(e)!r} "
                f"does not contain {msg_contains!r}"
            )
        return
    raise AssertionError(f"expected {exc_type.__name__}, nothing was raised")


def make_workspace(tmp: Path) -> dict:
    teaching = tmp / "teaching"; teaching.mkdir()
    repo = tmp / "repo"; repo.mkdir()
    (repo / "r2g-rtl2gds").mkdir()

    in_file = repo / "r2g-rtl2gds" / "design_cases" / "demo" / "rtl" / "demo.v"
    in_file.parent.mkdir(parents=True)
    in_file.write_text("module demo; endmodule\n")

    out_file = repo / "r2g-rtl2gds" / "design_cases" / "demo" / "synth" / "demo.synth.v"
    out_file.parent.mkdir(parents=True)
    out_file.write_text("// synth\nmodule demo; endmodule\n")

    log_file = out_file.parent / "synth.log"
    log_file.write_text(
        "Yosys 0.39\nNumber of cells: 42\nTop module: \\demo\n"
    )
    return {
        "teaching": teaching, "repo": repo,
        "in_file": in_file, "out_file": out_file, "log_file": log_file,
    }


def base_kwargs(ws):
    return dict(
        teaching_root=ws["teaching"], repo_root=ws["repo"],
        design="demo", stage="stage1", step="synthesis",
        command="yosys -s scripts/flow/run_synth.sh",
        inputs_files=[ws["in_file"]],
        outputs_files=[ws["out_file"], ws["log_file"]],
        start_ts="2026-05-29T08:12:03Z",
        end_ts="2026-05-29T08:13:47Z",
        exit_code=0, triggered_by="test", agent_backend="selftest/0.0",
    )


# ─── tests ──────────────────────────────────────────────────────────────────

def run_all() -> Result:
    r = Result()

    # canonical.py
    print("\n[1] canonical.py")

    def t_excludes_hash():
        assert canonical_json_bytes({"a": 1, "record_hash": "x"}) == b'{"a":1}'
    r.run("excludes record_hash", t_excludes_hash)

    def t_sorted():
        assert canonical_json_bytes({"z": 1, "a": 2}) == b'{"a":2,"z":1}'
    r.run("keys sorted", t_sorted)

    def t_compact():
        b = canonical_json_bytes({"a": 1, "b": 2})
        assert b" " not in b and b"\n" not in b
    r.run("compact, no whitespace", t_compact)

    def t_unicode():
        b = canonical_json_bytes({"d": "测试_设计"})
        assert "测试_设计".encode() in b
        assert b"\\u" not in b
    r.run("unicode preserved", t_unicode)

    def t_nan():
        expect_raises(ValueError, lambda: canonical_json_bytes({"x": math.nan}),
                      "NaN")
    r.run("NaN rejected", t_nan)

    def t_inf():
        expect_raises(ValueError, lambda: canonical_json_bytes({"x": math.inf}))
    r.run("inf rejected", t_inf)

    def t_none_allowed():
        canonical_json_bytes({"x": None})  # should NOT raise
    r.run("None permitted", t_none_allowed)

    def t_round_trip():
        rec = {"a": 1, "b": "two", "c": [3.0, None]}
        h = compute_record_hash(rec)
        assert len(h) == 64
        assert compute_record_hash(rec) == h
    r.run("hash round-trip", t_round_trip)

    def t_order_independence():
        assert compute_record_hash({"x": 1, "y": 2}) == \
               compute_record_hash({"y": 2, "x": 1})
    r.run("hash independent of key order", t_order_independence)

    def t_pinned():
        rec = {"design": "usb_cdc_top",
               "key_metrics": {"cell_count": 1842, "area_um2": None},
               "run_seq": 0}
        expected = (b'{"design":"usb_cdc_top",'
                    b'"key_metrics":{"area_um2":null,"cell_count":1842},'
                    b'"run_seq":0}')
        assert canonical_json_bytes(rec) == expected
        assert compute_record_hash(rec) == hashlib.sha256(expected).hexdigest()
    r.run("pinned fixture (autograder contract)", t_pinned)

    def t_verify():
        rec = {"a": 1}
        rec["record_hash"] = compute_record_hash(rec)
        assert verify_record_hash(rec) is True
        rec["a"] = 2
        assert verify_record_hash(rec) is False
    r.run("verify catches tampering", t_verify)

    # metrics_parsers.py
    print("\n[2] metrics_parsers.py")

    def t_registry_complete():
        expected = {"lint","simulation","synthesis","orfs_backend",
                    "timing_check","drc_klayout","lvs_klayout","rcx_openrcx",
                    "label_wirelength","label_congestion","label_timing",
                    "label_irdrop"}
        assert expected <= set(METRICS_PARSERS)
    r.run("all 12 steps registered", t_registry_complete)

    def t_unknown_step_loud():
        expect_raises(KeyError, lambda: get_parser("nope"), "unknown step")
    r.run("unknown step raises KeyError", t_unknown_step_loud)

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)

        def write(name, content):
            p = tmp / name; p.write_text(content); return {name: p}

        def t_lint_verilator():
            m = parse_lint(write("lint.log",
                "%Warning-WIDTH: a\n%Warning-CASE: b\n%Error: c\n"))
            assert m == {"warning_count": 2, "error_count": 1}
        r.run("lint: verilator markers", t_lint_verilator)

        def t_sim_pass():
            m = parse_simulation(write("sim.log",
                "TEST PASSED\ncycles: 1024\n"))
            assert m["tb_pass"] is True and m["sim_cycles"] == 1024
        r.run("simulation: TEST PASSED + cycles", t_sim_pass)

        def t_sim_indeterminate():
            m = parse_simulation(write("sim.log", "ran for a while\n"))
            assert m["tb_pass"] is None
        r.run("simulation: indeterminate is None not False", t_sim_indeterminate)

        def t_synth_full():
            m = parse_synthesis(write("synth.log",
                "Yosys 0.39\nNumber of cells: 1842\n"
                "Chip area for top module \\demo: 12483.50\n"
                "Top module: \\demo\n"))
            assert m["cell_count"] == 1842 and m["area_um2"] == 12483.50
            assert m["top_module"] == "demo"
        r.run("synthesis: yosys output", t_synth_full)

        def t_synth_unparseable():
            m = parse_synthesis(write("synth.log", "garbage\n"))
            assert m == {"cell_count": None, "area_um2": None, "top_module": None}
        r.run("synthesis: None on unparseable (NOT zero)", t_synth_unparseable)

        def t_orfs_json():
            m = parse_orfs_backend(write("6_report.json", json.dumps({
                "finish__timing__wns__worst": -0.12,
                "finish__timing__tns__total": -2.4,
                "finish__design__instance__count": 18000,
            })))
            assert abs(m["wns_ns"] - (-0.12)) < 1e-9
            assert m["instance_count"] == 18000
        r.run("orfs_backend: JSON report", t_orfs_json)

        def t_timing_minor():
            m = parse_timing_check(write("timing_report.txt",
                "tier: minor\nwns: -0.05 ns\ntns: -0.3 ns\n"))
            assert m == {"tier": "minor", "wns_ns": -0.05, "tns_ns": -0.3}
        r.run("timing_check: minor tier", t_timing_minor)

        def t_drc_count():
            m = parse_drc_klayout(write("result.rpt", "Total violations: 7\n"))
            assert m == {"violation_count": 7}
        r.run("drc: total line", t_drc_count)

        def t_drc_lyrdb():
            m = parse_drc_klayout(write("result.lyrdb",
                "<?xml ?>\n<item>a</item>\n<item>b</item>\n"))
            assert m == {"violation_count": 2}
        r.run("drc: lyrdb item count", t_drc_lyrdb)

        def t_lvs_pass():
            m = parse_lvs_klayout(write("result.rpt", "LVS PASS\n"))
            assert m["status"] == "PASS"
        r.run("lvs: pass marker", t_lvs_pass)

        def t_lvs_indeterminate_is_none():
            m = parse_lvs_klayout(write("result.rpt", "ran\n"))
            # Critical: indeterminate must NOT default to PASS or empty string
            assert m["status"] is None
        r.run("lvs: indeterminate is None (not PASS)", t_lvs_indeterminate_is_none)

        def t_csv_rowcount():
            m = parse_label_wirelength(write("wirelength.csv",
                "net,wl\na,1\nb,2\n"))
            assert m == {"row_count": 2}
        r.run("label CSV: row_count", t_csv_rowcount)

        def t_csv_header_only_is_zero():
            m = parse_label_wirelength(write("wirelength.csv", "net,wl\n"))
            assert m == {"row_count": 0}
        r.run("label CSV: header-only is 0 (real result)", t_csv_header_only_is_zero)

        def t_csv_missing_is_empty_dict():
            # Different signal from "0 rows": file not present at all
            m = parse_label_wirelength(write("unrelated.txt", "x"))
            assert m == {}
        r.run("label CSV: missing → {}", t_csv_missing_is_empty_dict)

    # append_ledger.py
    print("\n[3] append_ledger.py")

    def t_first_record_genesis():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            rec = append_record(**base_kwargs(ws))
            assert rec["prev_hash"] == "GENESIS"
            assert rec["run_seq"] == 0
            assert verify_record_hash(rec)
    r.run("first record uses GENESIS prev_hash", t_first_record_genesis)

    def t_chain_links():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            r1 = append_record(**base_kwargs(ws))
            kw = base_kwargs(ws); kw["step"] = "lint"
            r2 = append_record(**kw)
            assert r2["prev_hash"] == r1["record_hash"]
            assert r2["run_seq"] == 1
            assert verify_record_hash(r2)
    r.run("subsequent record links prev_hash", t_chain_links)

    def t_agent_direct_rejected():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            kw = base_kwargs(ws); kw["triggered_by"] = FORBIDDEN_TRIGGER
            expect_raises(PermissionError, lambda: append_record(**kw))
            assert not (ws["teaching"]/LEDGER_FILENAME).exists() or \
                   (ws["teaching"]/LEDGER_FILENAME).read_text() == ""
    r.run("agent_direct rejected, nothing written", t_agent_direct_rejected)

    def t_unknown_trigger():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            kw = base_kwargs(ws); kw["triggered_by"] = "made_up"
            expect_raises(ValueError, lambda: append_record(**kw))
    r.run("unknown trigger rejected", t_unknown_trigger)

    def t_invalid_stage():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            kw = base_kwargs(ws); kw["stage"] = "stage99"
            expect_raises(ValueError, lambda: append_record(**kw))
    r.run("invalid stage rejected", t_invalid_stage)

    def t_paths_normalized():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            rec = append_record(**base_kwargs(ws))
            for p in list(rec["inputs"]) + list(rec["outputs"]):
                assert p.startswith("<repo>/"), p
            flat = json.dumps(rec, ensure_ascii=False)
            assert str(ws["repo"].resolve()) not in flat
    r.run("paths normalized, no abs path leak", t_paths_normalized)

    def t_metrics_attached():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            rec = append_record(**base_kwargs(ws))
            assert rec["key_metrics"]["cell_count"] == 42
            assert rec["key_metrics"]["top_module"] == "demo"
    r.run("metrics parsed and attached", t_metrics_attached)

    def t_corruption_refuses():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            append_record(**base_kwargs(ws))
            (ws["teaching"]/LEDGER_FILENAME).open("a").write("not json\n")
            kw = base_kwargs(ws); kw["step"] = "lint"
            expect_raises(RuntimeError, lambda: append_record(**kw), "corrupt")
    r.run("corrupt tail → refuses to write more", t_corruption_refuses)

    def t_required_fields():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            rec = append_record(**base_kwargs(ws))
            required = {"record_version","prev_hash","record_hash","run_id",
                        "run_seq","design","stage","step","command",
                        "working_directory","tool_versions","inputs","outputs",
                        "key_metrics","start_ts","end_ts","duration_s",
                        "exit_code","agent_backend","r2g_commit",
                        "env_overrides_hash","triggered_by","notes"}
            missing = required - set(rec.keys())
            assert not missing, f"missing fields: {missing}"
    r.run("all 23 required fields present", t_required_fields)

    def t_duration_computed():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            rec = append_record(**base_kwargs(ws))
            assert abs(rec["duration_s"] - 104.0) < 0.001
    r.run("duration computed from ISO timestamps", t_duration_computed)

    def t_negative_duration_truthful():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            kw = base_kwargs(ws)
            kw["start_ts"], kw["end_ts"] = kw["end_ts"], kw["start_ts"]
            rec = append_record(**kw)
            # We DON'T silently fix bad timestamps — verifier flags it.
            assert rec["duration_s"] < 0
    r.run("negative duration recorded as-is (no silent fix)", t_negative_duration_truthful)

    def t_concurrent_writes():
        """Real OS-level concurrent writers."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            append_record(**base_kwargs(ws))  # seed
            n = 4

            # Spawn n subprocesses that each append once.
            procs = []
            for i, step in enumerate(["lint","simulation","drc_klayout",
                                      "lvs_klayout"]):
                code = f"""
import sys
sys.path.insert(0, {str(_HERE)!r})
from pathlib import Path
from append_ledger import append_record
append_record(
    teaching_root=Path({str(ws['teaching'])!r}),
    repo_root=Path({str(ws['repo'])!r}),
    design='demo', stage='stage1', step={step!r},
    command='x',
    inputs_files=[Path({str(ws['in_file'])!r})],
    outputs_files=[Path({str(ws['out_file'])!r})],
    start_ts='2026-05-29T08:12:03Z',
    end_ts='2026-05-29T08:12:04Z',
    exit_code=0, triggered_by='test',
)
"""
                p = subprocess.Popen([sys.executable, "-c", code],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
                procs.append(p)
            for p in procs:
                out, err = p.communicate(timeout=30)
                assert p.returncode == 0, err.decode(errors="replace")

            # Now verify chain end-to-end
            records = [json.loads(l) for l in
                       (ws["teaching"]/LEDGER_FILENAME).read_text().splitlines()]
            assert len(records) == n + 1
            prev = "GENESIS"
            for i, r_ in enumerate(records):
                assert r_["prev_hash"] == prev, f"chain broken at seq {i}"
                assert r_["run_seq"] == i
                assert verify_record_hash(r_)
                prev = r_["record_hash"]
    r.run("4 concurrent writers keep chain intact", t_concurrent_writes)

    def t_cli():
        """Smoke-test the CLI shape — flow scripts will invoke it this way."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            script = _HERE / "append_ledger.py"
            result = subprocess.run(
                [sys.executable, str(script),
                 "--teaching-root", str(ws["teaching"]),
                 "--repo-root", str(ws["repo"]),
                 "--design", "demo", "--stage", "stage1", "--step", "synthesis",
                 "--command", "yosys -s run.tcl",
                 "--inputs-glob", str(ws["in_file"]),
                 "--outputs-glob",
                    f"{ws['out_file']},{ws['log_file']}",
                 "--start-ts", "2026-05-29T08:12:03Z",
                 "--end-ts", "2026-05-29T08:13:47Z",
                 "--exit-code", "0", "--triggered-by", "test"],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert len(result.stdout.strip()) == 64
    r.run("CLI invocation works end-to-end", t_cli)

    def t_cli_rejects_agent_direct():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            script = _HERE / "append_ledger.py"
            result = subprocess.run(
                [sys.executable, str(script),
                 "--teaching-root", str(ws["teaching"]),
                 "--repo-root", str(ws["repo"]),
                 "--design", "demo", "--stage", "stage1", "--step", "synthesis",
                 "--command", "x",
                 "--start-ts", "2026-05-29T08:12:03Z",
                 "--end-ts", "2026-05-29T08:12:04Z",
                 "--exit-code", "0", "--triggered-by", "agent_direct"],
                capture_output=True, text=True,
            )
            assert result.returncode != 0
    r.run("CLI rejects --triggered-by agent_direct", t_cli_rejects_agent_direct)

    return r


if __name__ == "__main__":
    print("Running ledger self-tests...")
    print("=" * 70)
    result = run_all()
    print("=" * 70)
    print(f"\n{result.passed} passed, {result.failed} failed")
    if result.failed:
        print("\nFailures:")
        for f in result.failures:
            print(f"  - {f}")
    sys.exit(0 if result.failed == 0 else 1)
