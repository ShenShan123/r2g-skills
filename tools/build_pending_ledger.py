#!/usr/bin/env python3
"""Build a 'pending' campaign ledger for a platform from the SET-UP projects.

The honest source of truth for "which designs are ready to flow on platform P" is
each project's own ``constraints/config.mk`` (its ``export PLATFORM`` line), NOT the
old ledger or design_meta.json. run_orfs.sh builds against config.mk's PLATFORM —
so this builder enumerates ``design_cases/*/constraints/config.mk`` and emits one
``pending`` ledger row per project whose ``PLATFORM`` matches ``--platform``. That
guarantees the engineer_loop drives EVERY set-up RTL design through the full
sign-off flow, and that the ledger can never claim a platform the project isn't
actually configured for.

Typical use for a technology re-target ("new round" on asap7):
    python3 tools/setup_rtl_designs.py --platform asap7 --force     # config.mk -> asap7
    python3 tools/build_pending_ledger.py --platform asap7 \
        --out design_cases/_batch/asap7_campaign.jsonl

A/B arm scratch dirs (``<design>_abA_<strat>_<r>`` / ``_abB_``) are transient
campaign artifacts, never real designs to sign off — excluded by default.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARM_DIR = re.compile(r"_ab[AB]_")


def _read_kv(config_mk: Path, key: str):
    """Return the value of `export KEY = VALUE` from a config.mk, or None."""
    pat = re.compile(rf"^\s*export\s+{re.escape(key)}\s*=\s*(.+?)\s*$")
    try:
        for line in config_mk.read_text(encoding="utf-8").splitlines():
            m = pat.match(line)
            if m:
                return m.group(1).strip()
    except OSError:
        return None
    return None


def build(platform: str, design_cases: Path, *, exclude_arms: bool = True):
    """Yield ledger rows (dicts) for every project configured for `platform`."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, skipped = [], {"wrong_platform": 0, "no_config": 0, "arm_scratch": 0}
    for proj in sorted(p for p in design_cases.iterdir() if p.is_dir()):
        name = proj.name
        if name.startswith("_"):  # _batch, _dashboard, _setup_summary
            continue
        if exclude_arms and ARM_DIR.search(name):
            skipped["arm_scratch"] += 1
            continue
        config_mk = proj / "constraints" / "config.mk"
        if not config_mk.exists():
            skipped["no_config"] += 1
            continue
        proj_plat = _read_kv(config_mk, "PLATFORM")
        if proj_plat != platform:
            skipped["wrong_platform"] += 1
            continue
        rows.append({
            "design": name,
            "kind": "normal",
            "platform": platform,
            "project_path": str(proj.resolve()),
            "state": "pending",
            "ts": now,
        })
    return rows, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True,
                    help="only enumerate projects whose config.mk PLATFORM matches this")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "design_cases/_batch/campaign.jsonl",
                    help="output JSONL ledger path")
    ap.add_argument("--design-cases", type=Path,
                    default=ROOT / "design_cases",
                    help="root of set-up project dirs")
    ap.add_argument("--include-arms", action="store_true",
                    help="include _abA_/_abB_ scratch dirs (default: exclude)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite --out if it already exists")
    args = ap.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"ERROR: {args.out} exists. Pass --force to overwrite, or pick a new "
              f"--out (keep the prior round's ledger as immutable history).",
              file=sys.stderr)
        return 2

    rows, skipped = build(args.platform, args.design_cases,
                          exclude_arms=not args.include_arms)
    if not rows:
        print(f"ERROR: 0 projects configured for platform={args.platform}. Did you run "
              f"`setup_rtl_designs.py --platform {args.platform} --force` first?",
              file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print(f"Wrote {len(rows)} pending {args.platform} designs -> {args.out}")
    print(f"Skipped: {skipped['wrong_platform']} other-platform, "
          f"{skipped['no_config']} no-config, {skipped['arm_scratch']} arm-scratch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
