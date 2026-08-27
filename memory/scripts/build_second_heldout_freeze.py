#!/usr/bin/env python3
"""Build the second held-out evaluation snapshot after the training expansion.

The physical training database is the post-WP2 ``memory/tehm.sqlite``.  The
RTL training evidence is copied from the first freeze because the current
working database is physical-only; only RTL rows/rules are imported.  No
held-out task is captured or inserted here.  The output is a self-contained
read-only evaluation snapshot plus a manifest that records the firewall.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path


RTL_RULE = "domain = 'rtl'"


def _insert_rows(dst: sqlite3.Connection, src: sqlite3.Connection,
                 table: str, where: str = "1=1", params: tuple = ()) -> int:
    src.row_factory = sqlite3.Row
    rows = src.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
    if not rows:
        return 0
    cols = [x[1] for x in dst.execute(f"PRAGMA table_info({table})")]
    values = ",".join("?" for _ in cols)
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({values})"
    for row in rows:
        dst.execute(sql, tuple(row[c] for c in cols))
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--physical-db", type=Path,
                    default=Path("/data1/zhangdy/r2g-skills/memory/tehm.sqlite"))
    ap.add_argument("--rtl-freeze", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v1"))
    ap.add_argument("--output", type=Path,
                    default=Path("/data1/zhangdy/tehm-evidence-freeze-v2"))
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        raise SystemExit(f"refusing to overwrite existing freeze: {output}; use --overwrite")
    if output.exists():
        # This is an explicit, narrow output bundle cleanup, never a workspace
        # or source-tree delete.
        shutil.rmtree(output)
    db_path = output / "closed_loop" / "tehm.sqlite"
    db_path.parent.mkdir(parents=True)
    shutil.copy2(args.physical_db.resolve(), db_path)
    # Start with the post-WP2 physical artifact store, then union the frozen
    # RTL blobs.  Both stores are content-addressed; copying only one would
    # make an otherwise valid state fail H4 artifact verification.
    shutil.copytree(Path(args.physical_db).resolve().parent / "artifacts",
                    output / "closed_loop" / "artifacts")
    shutil.copytree(args.rtl_freeze.resolve() / "closed_loop" / "artifacts",
                    output / "closed_loop" / "artifacts", dirs_exist_ok=True)

    dst = sqlite3.connect(db_path)
    src = sqlite3.connect((args.rtl_freeze.resolve() / "closed_loop" /
                           "tehm.sqlite"))
    src.row_factory = sqlite3.Row
    # Import RTL training states/transitions/episodes/views and their source
    # rule.  Physical rows remain exactly those from the post-WP2 DB.
    copied = {}
    copied["tehm_states"] = _insert_rows(dst, src, "tehm_states", "domain='rtl'")
    rtl_training_transition_ids = [r[0] for r in src.execute(
        "SELECT DISTINCT s.transition_id FROM tehm_episode_steps s "
        "JOIN tehm_episodes e ON e.episode_id=s.episode_id "
        "WHERE e.domain='rtl'")]
    if rtl_training_transition_ids:
        marks = ",".join("?" for _ in rtl_training_transition_ids)
        copied["tehm_transitions"] = _insert_rows(
            dst, src, "tehm_transitions",
            f"action_domain LIKE 'rtl.%' AND transition_id IN ({marks})",
            tuple(rtl_training_transition_ids))
    else:
        copied["tehm_transitions"] = 0
    copied["tehm_episodes"] = _insert_rows(dst, src, "tehm_episodes", "domain='rtl'")
    episode_ids = [r[0] for r in src.execute(
        "SELECT episode_id FROM tehm_episodes WHERE domain='rtl'")]
    transition_ids = list(rtl_training_transition_ids)
    if episode_ids:
        marks = ",".join("?" for _ in episode_ids)
        copied["tehm_episode_steps"] = _insert_rows(
            dst, src, "tehm_episode_steps", f"episode_id IN ({marks})",
            tuple(episode_ids))
    if episode_ids or transition_ids:
        owners = episode_ids + transition_ids
        marks = ",".join("?" for _ in owners)
        copied["tehm_views"] = _insert_rows(
            dst, src, "tehm_views", f"owner_id IN ({marks})", tuple(owners))
    copied["tehm_rules"] = _insert_rows(dst, src, "tehm_rules", RTL_RULE)
    rule_ids = [r[0] for r in src.execute(
        "SELECT rule_id FROM tehm_rules WHERE domain='rtl'")]
    if rule_ids:
        marks = ",".join("?" for _ in rule_ids)
        copied["tehm_rule_sources"] = _insert_rows(
            dst, src, "tehm_rule_sources", f"rule_id IN ({marks})", tuple(rule_ids))
        copied["tehm_rule_status"] = _insert_rows(
            dst, src, "tehm_rule_status", f"rule_id IN ({marks})", tuple(rule_ids))
    dst.commit()
    counts = {}
    for table in ("tehm_states", "tehm_transitions", "tehm_episodes",
                  "tehm_views", "tehm_rules", "tehm_rule_sources",
                  "tehm_rule_status", "tehm_physical_effects"):
        counts[table] = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    dst.close(); src.close()

    manifest = {
        "version": "tehm-evidence-freeze-v2",
        "database": str(db_path),
        "source_physical_db": str(args.physical_db.resolve()),
        "source_rtl_freeze": str(args.rtl_freeze.resolve()),
        "counts": counts,
        "copied_rtl_rows": copied,
        "training_lineages": [
            "orfs-v2:aes", "orfs-v2:gcd", "orfs-v2:jpeg", "orfs-v2:riscv32i",
            "orfs-v3:ihp-sg13g2:gcd:base0", "orfs-v3:ihp-sg13g2:gcd:base1",
            "orfs-v3:ihp-sg13g2:gcd:base2", "orfs-v3:sky130hd:gcd:base0",
            "orfs-v3:sky130hd:gcd:base1", "orfs-v3:sky130hd:gcd:base2",
            "orfs-v3:sky130hs:gcd:base0", "orfs-v3:sky130hs:gcd:base1",
            "orfs-v3:sky130hs:gcd:base2", "orfs-v4:sky130hs:fifo:base0",
            "orfs-v4:sky130hs:fifo:base1", "orfs-v4:sky130hs:fifo:base2",
            "orfs-v4:sky130hs:uart:base0", "orfs-v4:sky130hs:uart:base1",
            "orfs-v4:sky130hs:uart:base2",
        ],
        "heldout_lineages": ["orfs-heldout-v3:spi",
                             "req_ack_bug4"],
        "firewall": {
            "disjoint": True,
            "heldout_not_captured": True,
            "heldout_not_in_database": {
                "orfs-heldout-v3:spi": True,
                "req_ack_bug4": True,
            },
            "mutation_policy": "held-out evaluation may mutate only a temporary DB copy",
        },
        "claims": "second frozen held-out snapshot; cluster-aware evaluation, not a universal benchmark",
    }
    (output / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
