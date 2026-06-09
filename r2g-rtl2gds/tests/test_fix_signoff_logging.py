"""Task 6: fix_signoff.sh records config_delta, env_flags, and symptom predicates."""
import json, os, subprocess, stat
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"

def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)

def test_log_iter_records_config_delta_env_and_predicates(tmp_path):
    proj = tmp_path / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    lvs_json = json.dumps({
        "status": "fail", "mismatch_class": "symmetric_matcher", "mismatch_count": 8,
        "net_mismatches_schematic_only": 3, "net_mismatches_layout_only": 3,
        "device_mismatches": 0, "circuit_swaps": 1})
    (proj / "reports" / "lvs.json").write_text(lvs_json)
    bindir = tmp_path / "bin"; bindir.mkdir()
    marker = proj / "reports" / ".r2g_done"
    # diagnose stub: first --next returns one strategy; after apply creates marker,
    # second --next returns STOP. --apply prints config_edits JSON and creates marker.
    _stub(bindir / "diagnose.sh", f'''
case "$*" in
  *--next*) if [[ -f "{marker}" ]]; then printf "STOP\\tfail\\tdone\\n"; else printf "lvs_same_nets_seed\\t\\trecheck\\n"; fi ;;
  *--apply*) printf '{{"applied":"lvs_same_nets_seed","config_edits":{{"LVS_SEED":"1"}}}}\\n'; : > "{marker}" ;;
esac
''')
    # extract_lvs stub: (re)writes the report so _count returns a number.
    _stub(bindir / "extract_lvs.sh", f'''cat > '{proj}/reports/lvs.json' <<'EOF'
{lvs_json}
EOF
''')
    _stub(bindir / "noop.sh", "exit 0\n")
    env = dict(os.environ)
    env.update({
        "R2G_DIAGNOSE": str(bindir / "diagnose.sh"),
        "R2G_RUN_LVS": str(bindir / "noop.sh"),
        "R2G_RUN_DRC": str(bindir / "noop.sh"),
        "R2G_RUN_ORFS": str(bindir / "noop.sh"),
        "R2G_EXTRACT_LVS": str(bindir / "extract_lvs.sh"),
        "R2G_EXTRACT_DRC": str(bindir / "noop.sh"),
        "ROUTE_FAST": "1",
    })
    res = subprocess.run(
        ["bash", str(FIX_SIGNOFF), str(proj), "nangate45", "--check", "lvs", "--max-iters", "2"],
        env=env, capture_output=True, text=True)
    log_file = proj / "reports" / "fix_log.jsonl"
    assert log_file.exists(), f"no fix_log. stdout={res.stdout}\nstderr={res.stderr}"
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert lines, f"fix_log is empty. stdout={res.stdout}\nstderr={res.stderr}"
    # The first line should be the applied iteration (iter 1, strategy=lvs_same_nets_seed)
    log = json.loads(lines[0])
    assert log["strategy"] == "lvs_same_nets_seed", f"unexpected first row: {log}"
    assert json.loads(log["config_delta"]) == {"LVS_SEED": "1"}, f"config_delta wrong: {log}"
    assert json.loads(log["env_flags"]).get("ROUTE_FAST") == "1", f"env_flags wrong: {log}"
    assert log["predicates"]["nets_balanced"] is True, f"predicates.nets_balanced wrong: {log}"
    assert log["predicates"]["same_cell_swap_present"] is True, f"predicates.same_cell_swap_present wrong: {log}"
