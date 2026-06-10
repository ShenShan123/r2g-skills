"""A/B arm plumbing: --rank-first + R2G_FIX_EXCLUDE/R2G_FIX_RANK_FIRST."""
import json
from pathlib import Path

import diagnose_signoff_fix as dsf


def _proj(tmp_path):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = x\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 30\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 5,
         "categories": {"M3_ANTENNA": {"count": 5}}}))
    return p


def test_rank_first_reorders_plan(tmp_path, capsys):
    proj = _proj(tmp_path)
    # default order on sky130hd: antenna_diode_iters then antenna_density_relief
    rc = dsf.main([str(proj), "--check", "drc", "--list",
                   "--rank-first", "antenna_density_relief"])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["strategies"][0]["id"] == "antenna_density_relief"


def test_rank_first_unknown_id_is_harmless(tmp_path, capsys):
    proj = _proj(tmp_path)
    rc = dsf.main([str(proj), "--check", "drc", "--list",
                   "--rank-first", "no_such_strategy"])
    assert rc == 0   # plan unchanged, no crash


def test_fix_signoff_env_passthrough_appears_in_diagnose_args(tmp_path):
    import os
    import subprocess
    SKILL = Path(__file__).resolve().parents[1]
    fake = tmp_path / "fake_diagnose.py"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "open(sys.argv[0] + '.args', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
        "print('STOP\\tresidual\\ttest')\n")
    fake.chmod(0o755)
    proj = _proj(tmp_path)
    env = dict(os.environ, R2G_DIAGNOSE=str(fake),
               R2G_FIX_EXCLUDE="abandoned_strategy",
               R2G_FIX_RANK_FIRST="hot_strategy", R2G_JOURNAL="0")
    subprocess.run(["bash", str(SKILL / "scripts/flow/fix_signoff.sh"),
                    str(proj), "sky130hd", "--check", "drc"],
                   capture_output=True, text=True, env=env)
    args = (fake.parent / (fake.name + ".args")).read_text()
    assert "--exclude abandoned_strategy" in args.replace("  ", " ")
    assert "--rank-first hot_strategy" in args
