"""learn_heuristics derives fix_trajectories (Tier-2) + fix_recipes (Tier-3)."""
from __future__ import annotations
import json
import knowledge_db
import learn_heuristics


def _ev(conn, **row):
    cols = ("fix_session_id", "design_family", "platform", "check_type",
            "violation_class", "iter", "strategy", "before_count", "after_count",
            "verdict", "cumulative_config_json")
    d = dict.fromkeys(cols)
    d.update(row)
    ph = ", ".join(f":{c}" for c in cols)
    conn.execute(f"INSERT INTO fix_events ({', '.join(cols)}) VALUES ({ph})", d)


def _seed(conn):
    # Episode 1 (resolved): density_relief failed, diode_repair cleared.
    _ev(conn, fix_session_id="s1", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=1,
        strategy="antenna_density_relief", before_count=147, after_count=147,
        verdict="no_change")
    _ev(conn, fix_session_id="s1", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=2,
        strategy="antenna_diode_repair", before_count=147, after_count=0,
        verdict="cleared", cumulative_config_json='{"SKIP_ANTENNA_REPAIR": "1"}')
    # Episode 2 (abandoned): diode_repair tried, never cleared.
    _ev(conn, fix_session_id="s2", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=1,
        strategy="antenna_diode_repair", before_count=9, after_count=3,
        verdict="win")


def test_learn_emits_trajectories_and_recipes(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed(conn)
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    data = learn_heuristics.learn(db, out)

    # Tier-3 recipes in heuristics.json
    rec = data["families"]["ethernet"]["platforms"]["nangate45"]["fix_recipes"]
    strat = rec["drc"]["M2_ANTENNA"]["strategies"]
    assert strat["antenna_diode_repair"]["successes"] == 1      # s1 cleared
    assert strat["antenna_diode_repair"]["attempts"] == 2       # s1 + s2
    assert strat["antenna_density_relief"]["failures"] == 1     # s1 no_change
    assert rec["drc"]["M2_ANTENNA"]["n_sessions"] == 2          # abandoned counted (survivorship)

    # Tier-2 trajectories materialized in the DB
    conn = knowledge_db.connect(db)
    traj = {r[0]: r for r in conn.execute(
        "SELECT fix_session_id, outcome, winning_strategy FROM fix_trajectories")}
    assert traj["s1"][1] == "resolved" and traj["s1"][2] == "antenna_diode_repair"
    assert traj["s2"][1] == "abandoned" and traj["s2"][2] is None
    conn.close()


def test_learn_fix_is_idempotent(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed(conn)
    conn.commit()
    conn.close()
    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db, out)
    learn_heuristics.learn(db, out)   # re-derive from scratch
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_trajectories").fetchone()[0] == 2
    conn.close()
