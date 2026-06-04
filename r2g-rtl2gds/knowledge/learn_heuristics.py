#!/usr/bin/env python3
"""Derive empirical per-family heuristics from runs.sqlite.

Usage:
  learn_heuristics.py [--db <path>] [--out <path>]

Writes knowledge/heuristics.json. Pure derivation — no network, no execution.
A family/platform pair is included only when at least MIN_SUCCESSFUL
successful runs exist. Every learned metric is derived from successful runs
only; failed runs still count toward ``sample_size`` and ``success_rate``
but contribute nothing to ``core_utilization``, ``place_density_lb_addon``,
``typical_cell_count`` or ``p90_elapsed_s``.

"Successful" is defined by ``knowledge_db.is_success`` — the single shared
predicate. It now ALSO admits signoff-positive ``partial`` runs (a run that
reached a final signed-off layout with clean DRC/LVS/RCX but whose
stage_log.jsonl was incomplete, so ingest left orfs_status != 'pass'). Absence
of all signoff data is still NOT a success. See ``knowledge_db.is_success``.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import statistics
import sys
from pathlib import Path

import knowledge_db

MIN_SUCCESSFUL = 3

# Single source of truth for "a learnable success" lives in knowledge_db.
# Thin alias for readability inside this module.
_is_success = knowledge_db.is_success


def _fetch_rows(conn) -> list[dict]:
    cur = conn.execute("SELECT * FROM runs")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, int(round(0.9 * (len(s) - 1))))
    return s[idx]


_SENTINEL = 1e30


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[idx]


def _family_platform_entry(runs: list[dict]) -> dict | None:
    successes = [r for r in runs if _is_success(r)]
    if len(successes) < MIN_SUCCESSFUL:
        return None

    cu_vals = [r["core_utilization"] for r in successes
               if r.get("core_utilization") is not None]
    pd_vals = [r["place_density_lb_addon"] for r in successes
               if r.get("place_density_lb_addon") is not None]
    cell_vals = [r["cell_count"] for r in successes
                 if r.get("cell_count") is not None]
    elapsed_vals = [r["total_elapsed_s"] for r in successes
                    if r.get("total_elapsed_s") is not None]

    cp_vals: list[float] = []
    d_fp_pl_ns, d_fp_pl_pct, d_pl_fin_ns, d_pl_fin_pct = [], [], [], []
    for r in successes:
        period = r.get("clock_period_ns")
        fp = r.get("floorplan_setup_ws")
        pl = r.get("place_setup_ws")
        fin = r.get("finish_setup_ws")
        if fin is None:
            fin = r.get("wns_ns")
        if period is not None and fin is not None and fin < _SENTINEL:
            cp_vals.append(period - fin)
        if (None not in (period, fp, pl, fin) and period > 0
                and max(fp, pl, fin) < _SENTINEL):
            d_fp_pl_ns.append(fp - pl)
            d_fp_pl_pct.append((fp - pl) / period)
            d_pl_fin_ns.append(pl - fin)
            d_pl_fin_pct.append((pl - fin) / period)

    entry: dict = {
        "sample_size": len(runs),
        "success_count": len(successes),
        "success_rate": len(successes) / len(runs),
    }
    if cu_vals:
        entry["core_utilization"] = {
            "min_safe": min(cu_vals),
            "max_safe": max(cu_vals),
            "median": statistics.median(cu_vals),
        }
    if pd_vals:
        entry["place_density_lb_addon"] = {
            "min_safe": min(pd_vals),
            "max_safe": max(pd_vals),
            "median": statistics.median(pd_vals),
        }
    if cell_vals:
        entry["typical_cell_count"] = int(statistics.median(cell_vals))
    if elapsed_vals:
        entry["p90_elapsed_s"] = _p90(elapsed_vals)
    if cp_vals:
        entry["closing_period"] = {
            "min": min(cp_vals),
            "p10": _quantile(cp_vals, 0.10),
            "median": statistics.median(cp_vals),
            "n": len(cp_vals),
        }
    if d_fp_pl_ns:
        entry["slack_deterioration"] = {
            "d_fp_pl": {"ns_p90": _quantile(d_fp_pl_ns, 0.90),
                        "pct_p90": _quantile(d_fp_pl_pct, 0.90)},
            "d_pl_fin": {"ns_p90": _quantile(d_pl_fin_ns, 0.90),
                         "pct_p90": _quantile(d_pl_fin_pct, 0.90)},
            "n": len(d_fp_pl_ns),
        }
    return entry


def learn(db_path: Path | str,
          out_path: Path | str) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch_rows(conn)

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    families: dict[str, dict] = {}
    for (fam, plat), group_rows in groups.items():
        entry = _family_platform_entry(group_rows)
        if entry is None:
            continue
        fam_obj = families.setdefault(fam, {"platforms": {}})
        fam_obj["platforms"][plat] = entry

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "families": families,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path,
                   default=knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json")
    args = p.parse_args()
    data = learn(args.db, args.out)
    total = sum(len(f["platforms"]) for f in data["families"].values())
    print(f"Wrote {args.out} ({len(data['families'])} families, "
          f"{total} family/platform entries, {data['source_run_count']} runs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
