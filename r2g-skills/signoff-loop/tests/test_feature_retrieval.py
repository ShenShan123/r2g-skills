"""Win 5 — feature-keyed retrieval-augmented config suggestion.

5a: presynth.py emits a PRE-ROUTE feature vector (available at suggestion time).
5c: suggest_config retrieves the k nearest CLEAN runs by z-score-normalized
feature distance — replacing the infer_family prefix lookup (245/303 singleton
families) — and seeds from their median config. Falls back to family medians when
no feature vector / too-small corpus; all safety clamps still apply.
"""
import json
from pathlib import Path

import knowledge_db
import presynth
import suggest_config as sc


# ── 5a: pre-route extractor ──────────────────────────────────────────────────

def _mk_synth_project(tmp_path, name, *, cells=None, util=None, period=None,
                      layers=None, platform="sky130hd"):
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    (p / "synth").mkdir()
    cfg = f"export DESIGN_NAME = {name}\nexport PLATFORM = {platform}\n"
    if util is not None:
        cfg += f"export CORE_UTILIZATION = {util}\n"
    if period is not None:
        cfg += f"export CLOCK_PERIOD = {period}\n"
    if layers is not None:
        cfg += f"export MAX_ROUTING_LAYER = {layers}\n"
    (p / "constraints" / "config.mk").write_text(cfg)
    if cells is not None:
        (p / "synth" / "synth.log").write_text(
            f"Number of cells:      {cells}\nNumber of wires:      {cells}\n")
    return p


def test_presynth_extracts_available_fields(tmp_path):
    p = _mk_synth_project(tmp_path, "d", cells=4096, util=40, period=10)
    f = presynth.extract_presynth_features(p)
    assert f["instance_count"] == 4096
    assert f["target_utilization"] == 40.0
    assert f["clock_period_ns"] == 10.0
    assert f["routing_layers"] == 5            # sky130hd default
    assert f["est_logic_depth"] == 12          # ceil(log2(4096))


def test_presynth_robust_to_missing_synth_log(tmp_path):
    p = _mk_synth_project(tmp_path, "d", util=30, platform="nangate45")
    f = presynth.extract_presynth_features(p)
    assert f["instance_count"] is None
    assert f["est_logic_depth"] is None
    assert f["routing_layers"] == 10           # nangate45 default; never raises


# ── normalization ────────────────────────────────────────────────────────────

def test_zscore_balances_mixed_scales():
    # instance_count in thousands vs utilization in tens — without normalization
    # instance_count dominates the Euclidean distance.
    vecs = [[1000, 200, 10, 20, 10, 5],
            [2000, 400, 11, 40, 10, 5],
            [3000, 300, 12, 30, 10, 5]]
    means, stds = sc._zscore_stats(vecs)
    norm = [sc._normalize(v, means, stds) for v in vecs]
    for j in range(len(sc.FEATURE_KEYS)):
        col = [n[j] for n in norm]
        m = sum(col) / len(col)
        sd = (sum((x - m) ** 2 for x in col) / len(col)) ** 0.5
        # each column is now unit-std (or 0 for a constant column) -> comparable.
        assert sd == 0.0 or abs(sd - 1.0) < 1e-9


# ── 5c: KNN retrieval ────────────────────────────────────────────────────────

def _seed_run(conn, name, vec, *, cu, pd, score):
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, platform, "
        "ingested_at, core_utilization, place_density_lb_addon, outcome_score, "
        "drc_status, lvs_status, presynth_features_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, "/p/" + name, name, "sky130hd", "t", cu, pd, score,
         "clean", "clean", json.dumps(vec)))


def _pin_heavy(i):
    return {"instance_count": 800 + i, "primary_io": 1500 - i, "est_logic_depth": 8,
            "target_utilization": 12, "clock_period_ns": 10, "routing_layers": 5}


def _cell_dense(i):
    return {"instance_count": 4000 + i, "primary_io": 40, "est_logic_depth": 30,
            "target_utilization": 40, "clock_period_ns": 10, "routing_layers": 5}


def test_knn_retrieves_topology_match_not_name_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "k.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    # cell-dense distractors share the TARGET's name prefix ('axi_'); pin-heavy
    # exemplars have a different prefix. Same outcome_score so DISTANCE decides.
    for i in range(6):
        _seed_run(conn, f"axi_dense_{i}", _cell_dense(i), cu=40, pd=0.20, score=0.9)
        _seed_run(conn, f"eth_pinny_{i}", _pin_heavy(i), cu=12, pd=0.22, score=0.9)
    conn.commit()
    conn.close()

    proj = _mk_synth_project(tmp_path, "axi_target", cells=820, util=30, period=10)
    (proj / "reports").mkdir()
    (proj / "reports" / "presynth_features.json").write_text(json.dumps(
        {"instance_count": 820, "primary_io": 1450, "est_logic_depth": 8,
         "target_utilization": 30, "clock_period_ns": 10, "routing_layers": 5}))

    rec = sc.recommend(proj, db_path=db)
    assert rec["learned_source"].startswith("features:knn")
    # seeded from the pin-heavy exemplars (CU 12), NOT the same-prefix dense ones (40).
    assert rec["recommendations"]["CORE_UTILIZATION"] <= 20


def test_retrieval_keeps_density_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "k.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    # neighbors with an unsafe low PLACE_DENSITY_LB_ADDON (0.02) must still be
    # clamped to the 0.10 hard floor AFTER retrieval (safety rails beat retrieval).
    for i in range(4):
        _seed_run(conn, f"low_{i}", _pin_heavy(i), cu=15, pd=0.02, score=0.9)
    conn.commit()
    conn.close()
    proj = _mk_synth_project(tmp_path, "low_target", cells=805, util=30, period=10)
    (proj / "reports").mkdir()
    (proj / "reports" / "presynth_features.json").write_text(json.dumps(_pin_heavy(0)))
    rec = sc.recommend(proj, db_path=db)
    assert rec["recommendations"]["PLACE_DENSITY_LB_ADDON"] >= 0.10


def test_no_feature_vector_falls_back_to_family_medians(tmp_path, monkeypatch):
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    db = tmp_path / "k.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    conn.close()
    # No reports/presynth_features.json -> retrieval skipped; recommend must still
    # produce a baseline (family-median path / params_by_size), not crash.
    proj = _mk_synth_project(tmp_path, "plain", cells=2000, util=30, period=10)
    rec = sc.recommend(proj, db_path=db)
    assert "CORE_UTILIZATION" in rec["recommendations"]
    assert rec["learned_source"] is None or not rec["learned_source"].startswith("features")
