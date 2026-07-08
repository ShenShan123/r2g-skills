"""Git-shareable / mergeable knowledge store (knowledge_sync.py).

Verified against REAL failure designs two ways (per the user's "use failure designs
as test cases" directive):

  * production-SHAPED synthetic stores seeded with the EXACT failure signatures the
    live writer emits — FLW-0024 die-too-small place abort, GRT-0116 route congestion,
    PDN-0185 floorplan strap, PPL-0024 IO-overflow — with 'test_'-prefixed names so the
    privacy convention (test_honesty_invariants.assert_synthetic_names) holds; and
  * a full round-trip over a COPY of the committed knowledge.sqlite (all ~135 real
    fail rows, every real failure design), gated to the store actually being present.

The honesty contract is the point: a merge is ADDITIVE and is ROLLED BACK if it would
make the store lie (H3 parity / Gate-A / derivability). Surrogate AUTOINCREMENT ids
(ab_trials.trial_id, failure_events.id, ...) must NEVER fuse two operators' unrelated
rows — the merge dedups by NATURAL CONTENT KEY.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
if str(_KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_DIR))
import knowledge_db  # noqa: E402
import knowledge_sync as ks  # noqa: E402
import honesty  # noqa: E402

_REAL_STORE = _KNOWLEDGE_DIR / "knowledge.sqlite"

# Real failure SIGNATURES the live ingest writer emits (from the committed corpus),
# reused here with synthetic 'test_' design names. Each tuple: (stage, signature).
FLW0024 = ("place", "orfs-fail-place-FLW-0024")    # die-too-small place abort
GRT0116 = ("route", "orfs-fail-route-GRT-0116")    # route congestion
PDN0185 = ("floorplan", "orfs-fail-floorplan-PDN-0185")
PPL0024 = ("place", "orfs-fail-place-PPL-0024")    # IO pins exceed positions


# ── seeding (production-shaped) ──────────────────────────────────────────────
def _open(db_path: Path) -> sqlite3.Connection:
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn)
    return conn


def _add_fail(conn, *, run_id, name, platform, stage, signature, detail="[ERROR] seeded"):
    """A production-shaped backend-abort run: orfs_status='fail' string + the matching
    'orfs-fail-<stage>[-CODE]' failure_event (exactly what ingest_run writes)."""
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, design_family, "
        "platform, ingested_at, orfs_status, orfs_fail_stage) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_id, f"/proj/{run_id}", name, name, platform,
         "2026-06-23T00:00:00Z", "fail", stage))
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) VALUES (?,?,?,?)",
        (run_id, stage, signature, detail))


def _add_ab_trial(conn, *, symptom_id="sym", design_class="logic/small",
                  platform="nangate45", strategy="relief", verdict="win"):
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "arm_a_run_id, arm_b_run_id, verdict, ts) VALUES (?,?,?,?,?,?,?,?)",
        (symptom_id, design_class, platform, strategy, "A", "B", verdict,
         "2026-06-23T00:00:00Z"))


def _seed_operator(db_path, fails, *, with_ab=True) -> sqlite3.Connection:
    """An honest operator store: each fail carries its event, plus an ab_trial so
    Gate-A (ab_trials non-empty when fail/partial rows exist) is satisfied."""
    conn = _open(db_path)
    for f in fails:
        _add_fail(conn, **f)
    if with_ab:
        _add_ab_trial(conn, platform=fails[0]["platform"])
    conn.commit()
    return conn


# ── determinism ──────────────────────────────────────────────────────────────
def test_export_is_deterministic_byte_identical(tmp_path):
    conn = _seed_operator(tmp_path / "op.sqlite", [
        dict(run_id="r1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1]),
        dict(run_id="r2", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1]),
    ])
    conn.close()
    m1 = ks.export_bundle(tmp_path / "op.sqlite", tmp_path / "b1")
    m2 = ks.export_bundle(tmp_path / "op.sqlite", tmp_path / "b2")
    assert m1["digest"] == m2["digest"]
    # every NDJSON file is byte-for-byte identical (git would show a 0-line diff).
    for f in (tmp_path / "b1").iterdir():
        assert f.read_bytes() == (tmp_path / "b2" / f.name).read_bytes(), f.name


def test_export_sorted_rows_are_stable_under_insert_order(tmp_path):
    """Two stores with the SAME rows inserted in a DIFFERENT order export identically
    (rows are sorted by natural key), so git diffs reflect content, not write order."""
    specs = [
        dict(run_id="z9", name="test_a", platform="nangate45", stage=PPL0024[0],
             signature=PPL0024[1]),
        dict(run_id="a1", name="test_b", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1]),
    ]
    c1 = _seed_operator(tmp_path / "f.sqlite", specs); c1.close()
    c2 = _seed_operator(tmp_path / "r.sqlite", list(reversed(specs))); c2.close()
    d1 = ks.export_bundle(tmp_path / "f.sqlite", tmp_path / "bf")["digest"]
    d2 = ks.export_bundle(tmp_path / "r.sqlite", tmp_path / "br")["digest"]
    assert d1 == d2


# ── import round-trip ────────────────────────────────────────────────────────
def test_import_rebuild_is_lossless(tmp_path):
    conn = _seed_operator(tmp_path / "op.sqlite", [
        dict(run_id="r1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1]),
        dict(run_id="r2", name="test_fp", platform="nangate45", stage=PDN0185[0],
             signature=PDN0185[1]),
    ])
    conn.close()
    m_src = ks.export_bundle(tmp_path / "op.sqlite", tmp_path / "bundle")
    counts = ks.import_bundle(tmp_path / "bundle", tmp_path / "rebuilt.sqlite")
    assert counts["runs"] == 2 and counts["failure_events"] == 2
    # re-export the rebuilt DB: identical digest == perfect fidelity.
    m_dst = ks.export_bundle(tmp_path / "rebuilt.sqlite", tmp_path / "bundle2")
    assert m_dst["digest"] == m_src["digest"]
    # and the rebuilt store is honest.
    rc = sqlite3.connect(tmp_path / "rebuilt.sqlite")
    assert honesty.run_all(rc)[0] is True
    rc.close()


def test_import_refuses_to_clobber_existing_store(tmp_path):
    conn = _seed_operator(tmp_path / "op.sqlite", [
        dict(run_id="r1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    conn.close()
    ks.export_bundle(tmp_path / "op.sqlite", tmp_path / "bundle")
    # target already has runs -> import must refuse (use merge instead).
    with pytest.raises(FileExistsError):
        ks.import_bundle(tmp_path / "bundle", tmp_path / "op.sqlite")
    # ...unless overwrite is explicit.
    ks.import_bundle(tmp_path / "bundle", tmp_path / "op.sqlite", overwrite=True)


# ── merge (cross-operator union) ─────────────────────────────────────────────
def test_merge_unions_two_operators_additively(tmp_path):
    """Operator A (sky130 route fail) + operator B (nangate45 FLW-0024 place abort).
    Merge B into A: A gains B's run + event + trial; H3/Gate-A hold; merge APPLIED."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    ca.close()
    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.close()

    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bbundle")
    rep = ks.merge_bundle(tmp_path / "Bbundle", tmp_path / "A.sqlite")
    assert rep["applied"] is True and rep["honest"] is True
    assert rep["tables"]["runs"]["added"] == 1
    assert rep["tables"]["failure_events"]["added"] == 1
    assert rep["tables"]["ab_trials"]["added"] == 1

    a = sqlite3.connect(tmp_path / "A.sqlite")
    assert a.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    # the FLW-0024 abort from B is now in A's store, with its event (H3 intact).
    assert a.execute(
        "SELECT COUNT(*) FROM failure_events WHERE signature=?",
        (FLW0024[1],)).fetchone()[0] == 1
    assert honesty.run_all(a)[0] is True
    a.close()


def test_merge_is_idempotent(tmp_path):
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    ca.close()
    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.close()
    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    rep2 = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    assert sum(t["added"] for t in rep2["tables"].values()) == 0


def test_merge_refuses_dishonest_bundle_and_rolls_back(tmp_path):
    """A bundle that brings a 'fail' run WITHOUT its failure_event would break H3.
    The merge must be REFUSED (rolled back) — the store is never poisoned."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    before = ca.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    ca.close()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text(json.dumps(
        {"bundle_format_version": 1, "generation": 1, "tables": {}, "digest": "x"}))
    (bad / "runs.ndjson").write_text(json.dumps({
        "run_id": "evil", "project_path": "/p/evil", "design_name": "test_x",
        "design_family": "test_x", "platform": "nangate45",
        "ingested_at": "2026-06-23T00:00:00Z", "orfs_status": "fail",
        "orfs_fail_stage": "place"}) + "\n")
    # deliberately NO failure_events.ndjson -> the evil fail run has no event.

    rep = ks.merge_bundle(bad, tmp_path / "A.sqlite")
    assert rep["applied"] is False and rep["honest"] is False
    parity = next(r for r in rep["honesty_report"] if r["name"] == "fail_event_parity")
    assert parity["ok"] is False

    a = sqlite3.connect(tmp_path / "A.sqlite")
    assert a.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before
    assert a.execute("SELECT COUNT(*) FROM runs WHERE run_id='evil'").fetchone()[0] == 0
    assert honesty.run_all(a)[0] is True  # untouched, still honest
    a.close()


def test_merge_dry_run_writes_nothing(tmp_path):
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    ca.close()
    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.close()
    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    rep = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite", dry_run=True)
    assert rep["applied"] is False and rep["honest"] is True  # honest, just not applied
    a = sqlite3.connect(tmp_path / "A.sqlite")
    assert a.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    a.close()


def test_merge_does_not_fuse_colliding_surrogate_ids(tmp_path):
    """The crux of why a binary-blob merge is unsafe: both operators have
    ab_trials.trial_id=1 and failure_events.id=1 for DIFFERENT rows. A naive INSERT
    keyed on the surrogate id would FUSE them; the natural-key merge keeps BOTH."""
    ca = _open(tmp_path / "A.sqlite")
    _add_fail(ca, run_id="a1", name="test_a", platform="nangate45", stage=FLW0024[0],
              signature=FLW0024[1])                    # failure_events.id == 1
    _add_ab_trial(ca, symptom_id="symA", strategy="stratA")  # trial_id == 1
    ca.commit()
    assert ca.execute("SELECT id FROM failure_events").fetchone()[0] == 1
    assert ca.execute("SELECT trial_id FROM ab_trials").fetchone()[0] == 1
    ca.close()

    cb = _open(tmp_path / "B.sqlite")
    _add_fail(cb, run_id="b1", name="test_b", platform="sky130hd", stage=GRT0116[0],
              signature=GRT0116[1])                    # ALSO failure_events.id == 1
    _add_ab_trial(cb, symptom_id="symB", strategy="stratB")  # ALSO trial_id == 1
    cb.commit()
    cb.close()

    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    rep = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    assert rep["applied"] is True

    a = sqlite3.connect(tmp_path / "A.sqlite")
    # BOTH trials survive (re-keyed locally) — distinct content, not fused.
    assert a.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0] == 2
    strategies = {r[0] for r in a.execute("SELECT strategy FROM ab_trials")}
    assert strategies == {"stratA", "stratB"}
    # both fail runs + both events kept (failure_events.id re-assigned, not collided).
    assert a.execute("SELECT COUNT(*) FROM failure_events").fetchone()[0] == 2
    assert a.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    a.close()


def test_merge_reports_recipe_status_conflict_keeping_local(tmp_path):
    """A promoted by operator A, the same recipe 'candidate' for operator B. The merge
    KEEPS A's promoted (local wins — never silently flip a lifecycle decision) and
    REPORTS the conflict for operator review."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    ca.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, generation, updated_at) VALUES "
        "('sid','logic/small','nangate45','die_resize','promoted',5,'2026-06-23T00:00:00Z')")
    ca.commit()
    ca.close()

    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, generation, updated_at) VALUES "
        "('sid','logic/small','nangate45','die_resize','candidate',9,'2026-06-22T00:00:00Z')")
    cb.commit()
    cb.close()

    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    rep = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    assert rep["applied"] is True
    assert len(rep["recipe_conflicts"]) == 1
    c = rep["recipe_conflicts"][0]
    assert c["local"] == "promoted" and c["incoming"] == "candidate"

    a = sqlite3.connect(tmp_path / "A.sqlite")
    status = a.execute(
        "SELECT status FROM recipe_status WHERE strategy='die_resize'").fetchone()[0]
    assert status == "promoted"   # local kept, never silently overwritten
    a.close()


def test_merge_takes_max_generation(tmp_path):
    """meta.generation merges as max(local, incoming) so the store's counter never
    sits below an imported recipe's generation."""
    ca = _open(tmp_path / "A.sqlite")
    ca.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('generation','5')")
    ca.commit()
    ca.close()
    cb = _open(tmp_path / "B.sqlite")
    cb.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('generation','42')")
    cb.commit()
    cb.close()
    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    a = sqlite3.connect(tmp_path / "A.sqlite")
    assert a.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0] == "42"
    a.close()


def test_merge_db_directly_without_bundle(tmp_path):
    """merge_db unions another knowledge.sqlite with no intermediate bundle file."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_crypto", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    ca.close()
    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.close()
    rep = ks.merge_db(tmp_path / "B.sqlite", tmp_path / "A.sqlite")
    assert rep["applied"] is True and rep["tables"]["runs"]["added"] == 1


# ── adversarial-review regressions (2026-06-23) ──────────────────────────────
def test_merge_refuses_event_on_nonfail_run(tmp_path):
    """Review finding #1 (the inverse-H3 hole): a run_id COLLISION where local has the
    run as 'partial' (honest-incomplete) and the bundle has the SAME run_id as 'fail'
    WITH its orfs-fail event. The merge keeps local 'partial' (runs.added=0) but would
    add the event -> an orfs-fail event on a non-fail run (the fail/partial conflation
    CLAUDE.md forbids). The 5th honesty gate must REFUSE the merge."""
    ca = _open(tmp_path / "A.sqlite")
    ca.execute(
        "INSERT INTO runs (run_id, project_path, design_name, design_family, platform, "
        "ingested_at, orfs_status, orfs_fail_stage) VALUES "
        "('rc','/p/rc','test_d','test_d','nangate45','t','partial','place')")
    _add_ab_trial(ca)                       # Gate-A green so we isolate the new gate
    ca.commit()
    ca.close()
    cb = _open(tmp_path / "B.sqlite")
    cb.execute(
        "INSERT INTO runs (run_id, project_path, design_name, design_family, platform, "
        "ingested_at, orfs_status, orfs_fail_stage) VALUES "
        "('rc','/p/rc','test_d','test_d','nangate45','t','fail','place')")
    cb.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) "
        "VALUES ('rc','place','orfs-fail-place-FLW-0024','x')")
    cb.commit()
    cb.close()

    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    rep = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    assert rep["applied"] is False and rep["honest"] is False
    g = next(r for r in rep["honesty_report"] if r["name"] == "no_event_on_nonfail_run")
    assert g["ok"] is False
    a = sqlite3.connect(tmp_path / "A.sqlite")   # store untouched
    assert a.execute("SELECT orfs_status FROM runs WHERE run_id='rc'").fetchone()[0] == "partial"
    assert a.execute("SELECT COUNT(*) FROM failure_events WHERE run_id='rc'").fetchone()[0] == 0
    a.close()


def test_merge_imports_absent_recipe_as_inert_shadow(tmp_path):
    """Review finding #3: a recipe B DEMOTED, absent on A, must import as inert 'shadow'
    (NOT verbatim 'demoted' — that would silently suppress the strategy on A) and be
    reported, so A's own A/B re-validates B's verdict."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_c", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    ca.close()
    cb = _seed_operator(tmp_path / "B.sqlite", [
        dict(run_id="b1", name="test_m", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    cb.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, generation, updated_at, provenance) VALUES "
        "('sid','logic/small','nangate45','die_resize','demoted',9,'t','ab_trial:7')")
    cb.commit()
    cb.close()
    ks.export_bundle(tmp_path / "B.sqlite", tmp_path / "Bb")
    rep = ks.merge_bundle(tmp_path / "Bb", tmp_path / "A.sqlite")
    assert rep["applied"] is True
    assert len(rep["recipe_imports"]) == 1
    assert rep["recipe_imports"][0]["incoming"] == "demoted"
    a = sqlite3.connect(tmp_path / "A.sqlite")
    row = a.execute(
        "SELECT status, provenance FROM recipe_status WHERE strategy='die_resize'"
    ).fetchone()
    assert row[0] == "shadow"            # inert hypothesis, not the imported 'demoted'
    assert "merge:demoted" in row[1]
    a.close()


def test_export_digest_invariant_under_insert_order_with_dup_key(tmp_path):
    """Review finding #4: two ab_trials sharing the SAME natural key but differing on a
    non-key column (verdict) must export identically regardless of physical insert order
    — the total-order (natural key THEN full row) tie-break."""
    def seed(p, order):
        c = _open(p)
        c.execute(
            "INSERT INTO runs (run_id, project_path, design_name, design_family, "
            "platform, ingested_at, orfs_status, orfs_fail_stage) VALUES "
            "('r','/p','test_d','test_d','nangate45','t','fail','place')")
        c.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES ('r','place','orfs-fail-place','x')")
        for v in order:
            c.execute(
                "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
                "arm_a_run_id, arm_b_run_id, verdict, ts) VALUES "
                "('s','c','nangate45','st',NULL,NULL,?,'2026-01-01T00:00:00Z')", (v,))
        c.commit()
        c.close()
    seed(tmp_path / "f.sqlite", ["win", "loss"])
    seed(tmp_path / "r.sqlite", ["loss", "win"])
    d1 = ks.export_bundle(tmp_path / "f.sqlite", tmp_path / "bf")["digest"]
    d2 = ks.export_bundle(tmp_path / "r.sqlite", tmp_path / "br")["digest"]
    assert d1 == d2


def test_merge_refuses_dangling_foreign_key(tmp_path):
    """Review finding #5: a bundle whose failure_event references a run absent from BOTH
    local and the bundle must be REFUSED with a clean fk_violations report (not a bare
    IntegrityError crash), and the store must be untouched."""
    ca = _seed_operator(tmp_path / "A.sqlite", [
        dict(run_id="a1", name="test_c", platform="sky130hd", stage=GRT0116[0],
             signature=GRT0116[1])])
    before = ca.execute("SELECT COUNT(*) FROM failure_events").fetchone()[0]
    ca.close()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text(json.dumps(
        {"bundle_format_version": 1, "generation": 1, "tables": {}, "digest": "x"}))
    (bad / "failure_events.ndjson").write_text(json.dumps({
        "run_id": "ghost", "stage": "place", "signature": "orfs-fail-place",
        "detail": "x"}) + "\n")
    rep = ks.merge_bundle(bad, tmp_path / "A.sqlite")
    assert rep["applied"] is False
    assert rep["fk_violations"], "dangling FK should be reported, not crash"
    a = sqlite3.connect(tmp_path / "A.sqlite")
    assert a.execute("SELECT COUNT(*) FROM failure_events").fetchone()[0] == before
    assert a.execute(
        "SELECT COUNT(*) FROM failure_events WHERE run_id='ghost'").fetchone()[0] == 0
    a.close()


# ── status / drift ───────────────────────────────────────────────────────────
def test_status_detects_drift_after_new_run(tmp_path):
    conn = _seed_operator(tmp_path / "op.sqlite", [
        dict(run_id="r1", name="test_mul", platform="nangate45", stage=FLW0024[0],
             signature=FLW0024[1])])
    conn.close()
    ks.export_bundle(tmp_path / "op.sqlite", tmp_path / "store")
    out = ks.status(tmp_path / "op.sqlite", tmp_path / "store")
    assert out["in_sync"] is True and out["honest"] is True

    # add a row -> committed bundle is now stale -> status must flag drift.
    conn = sqlite3.connect(tmp_path / "op.sqlite")
    _add_fail(conn, run_id="r2", name="test_fp", platform="nangate45",
              stage=PDN0185[0], signature=PDN0185[1])
    conn.commit()
    conn.close()
    out2 = ks.status(tmp_path / "op.sqlite", tmp_path / "store")
    assert out2["in_sync"] is False
    drift_tables = {d["table"] for d in out2["drift"]}
    assert "runs" in drift_tables and "failure_events" in drift_tables


# ── REAL failure designs: round-trip the committed corpus ────────────────────
@pytest.mark.skipif(not _REAL_STORE.exists(),
                    reason="committed knowledge.sqlite not present")
def test_real_store_roundtrip_is_lossless_and_honest(tmp_path):
    """Export -> import -> re-export the COMMITTED store (all ~135 real fail rows,
    every real failure design) and assert byte-identical fidelity + honesty. This is
    the broadest 'real failure designs as test cases' check."""
    copy = tmp_path / "real.sqlite"
    shutil.copy(_REAL_STORE, copy)
    m_src = ks.export_bundle(copy, tmp_path / "b_src")
    ks.import_bundle(tmp_path / "b_src", tmp_path / "rebuilt.sqlite")
    m_dst = ks.export_bundle(tmp_path / "rebuilt.sqlite", tmp_path / "b_dst")
    assert m_dst["digest"] == m_src["digest"], "round-trip changed store content"

    rc = sqlite3.connect(tmp_path / "rebuilt.sqlite")
    assert honesty.run_all(rc)[0] is True, "rebuilt real store fails an honesty gate"
    # a real FLW-0024 place abort (the dominant nangate45 unseen-crash bucket) and a
    # real route-congestion abort survived the rebuild with their events.
    n_flw = rc.execute(
        "SELECT COUNT(*) FROM failure_events WHERE signature LIKE 'orfs-fail-place-FLW-0024'"
    ).fetchone()[0]
    n_grt = rc.execute(
        "SELECT COUNT(*) FROM failure_events WHERE signature LIKE 'orfs-fail-route-%'"
    ).fetchone()[0]
    assert n_flw >= 1, "FLW-0024 place aborts lost in round-trip"
    assert n_grt >= 1, "route aborts lost in round-trip"
    rc.close()


@pytest.mark.skipif(not _REAL_STORE.exists(),
                    reason="committed knowledge.sqlite not present")
def test_real_store_passes_honesty_gates():
    """Plan §1.3: run the honesty invariants against the REAL committed store in CI,
    not just mocks. A schema/shape drift that re-breaks the int/str or failure_events-
    parity invariant fails the build here, on real data."""
    conn = knowledge_db.connect(_REAL_STORE)
    try:
        ok, report = honesty.run_all(conn)
    finally:
        conn.close()
    assert ok is True, honesty.format_report(report)


@pytest.mark.skipif(not (_KNOWLEDGE_DIR / "store" / "manifest.json").exists(),
                    reason="no committed bundle (knowledge/store/) yet")
@pytest.mark.skipif(not _REAL_STORE.exists(),
                    reason="committed knowledge.sqlite not present")
def test_committed_bundle_in_sync_with_db():
    """Plan §2.3 drift gate: the committed text bundle (knowledge/store/) MUST match the
    committed knowledge.sqlite by digest. If this is red, the bundle is stale — someone
    changed the DB without re-exporting. Fix: `python3 knowledge/knowledge_sync.py export`.
    A stale shared bundle would transfer WRONG experience to a new user."""
    out = ks.status(_REAL_STORE, _KNOWLEDGE_DIR / "store")
    assert out["in_sync"] is True, (
        f"bundle drift {out.get('drift')} — run knowledge_sync.py export")


@pytest.mark.skipif(not _REAL_STORE.exists(),
                    reason="committed knowledge.sqlite not present")
def test_real_store_merges_into_empty_without_loss(tmp_path):
    """A NEW user bootstraps an empty store, then merges the committed corpus into it
    via the bundle: every real run/trial transfers and the result is honest."""
    src_copy = tmp_path / "real.sqlite"
    shutil.copy(_REAL_STORE, src_copy)
    n_runs = sqlite3.connect(src_copy).execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    ks.export_bundle(src_copy, tmp_path / "corpus")

    fresh = tmp_path / "fresh.sqlite"
    _open(fresh).close()   # empty, schema-only
    rep = ks.merge_bundle(tmp_path / "corpus", fresh)
    assert rep["applied"] is True and rep["honest"] is True
    assert rep["tables"]["runs"]["added"] == n_runs
    f = sqlite3.connect(fresh)
    assert f.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == n_runs
    f.close()

    # Idempotency on the REAL corpus: merging the same bundle again adds NOTHING.
    # This is the regression guard for NULL-in-natural-key tables (all 24 ab_trials and
    # all 200 escalations carry a NULL identity column) — they must dedup by full-row
    # content, else a second merge would either duplicate or silently drop them.
    rep2 = ks.merge_bundle(tmp_path / "corpus", fresh)
    assert sum(t["added"] for t in rep2["tables"].values()) == 0, rep2["tables"]
