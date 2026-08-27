"""Stage-scoped Recipe + A/B eligibility (RMD-HO-P1-02, held-out V3 P1-HO-02).

rtl-acquire wrote `acquire_exclude` into the SIGNOFF repair ledger; ingest
projected it into `fix_events`; the learner made it a symptom-keyed recipe; and
`_known_apply_strategy` admitted it because its fix_events fallback accepted ANY
strategy with a non-empty historical verdict. `plan_arms_for_candidates` has
platform scoping but had no action-domain filter, so it planned PHYSICAL A/B arms
over synth-only acquisition workspaces: 4 inconclusive trials per platform per
cohort, 12 wasted experiments across three platforms, and an unapplyable
candidate kept alive forever.

The guard is two-layered on purpose — refused at enqueue (the lifecycle owns what
may enter the queue) AND at plan time (defence in depth for stores that predate
the filter, which park_foreign_domain then heals).
"""
import action_domain
import engineer_loop as el
import knowledge_db
import recipe_lifecycle

ACQ = dict(symptom_id="a" * 16, design_class="misc/small",
           platform="sky130hs", strategy="acquire_exclude")
SIGNOFF = dict(symptom_id="b" * 16, design_class="misc/small",
               platform="sky130hs", strategy="density_relief")


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _fix_event(conn, strategy, check_type, verdict="no_change"):
    # (fix_session_id, iter, strategy) is UNIQUE — key the session on the check so
    # one strategy can carry evidence from more than one domain.
    conn.execute(
        "INSERT INTO fix_events (fix_session_id, strategy, check_type, verdict, iter) "
        "VALUES (?,?,?,?,0)", (f"s-{strategy}-{check_type}", strategy, check_type, verdict))
    conn.commit()


# --- domain derivation -------------------------------------------------------

def test_acquisition_prefixes_are_recognised():
    for s in ("acquire_exclude", "acquire_retry", "acquisition_defer", "rtl_acquire_x"):
        assert action_domain.domain_of(s) == action_domain.ACQUISITION
        assert not action_domain.is_signoff_domain(s)


def test_unknown_names_stay_open_for_the_evidence_check():
    # A future signoff strategy not yet in the static catalog must not be
    # fail-closed by NAME — the evidence test decides.
    assert action_domain.domain_of("some_new_drc_trick") == action_domain.UNKNOWN
    assert action_domain.is_signoff_domain("some_new_drc_trick")


def test_synth_memory_relax_is_signoff_not_acquisition():
    # The backend synth-abort recovery rides check_type='orfs_stage'; only the
    # ACQUISITION frontend rows use check='synth'. That asymmetry is the whole
    # discriminating power of the evidence test.
    assert action_domain.is_signoff_domain("synth_memory_relax")
    assert "synth" not in action_domain.SIGNOFF_CHECK_TYPES
    assert "orfs_stage" in action_domain.SIGNOFF_CHECK_TYPES


# --- _known_apply_strategy ---------------------------------------------------

def test_acquire_exclude_is_refused_even_with_fix_event_history(tmp_path):
    conn = _conn(tmp_path)
    _fix_event(conn, "acquire_exclude", "synth")
    assert el._known_apply_strategy(conn, "acquire_exclude") is False


def test_catalog_signoff_strategy_still_admitted(tmp_path):
    conn = _conn(tmp_path)
    assert el._known_apply_strategy(conn, "density_relief") is True


def test_unknown_strategy_needs_signoff_domain_evidence(tmp_path):
    conn = _conn(tmp_path)
    # Only acquisition-domain evidence: not proof of a signoff application path.
    _fix_event(conn, "mystery_fix", "synth")
    assert el._known_apply_strategy(conn, "mystery_fix") is False
    # Real signoff evidence vouches for it (the P0-6 stale-catalog fallback).
    _fix_event(conn, "mystery_fix", "drc", verdict="cleared")
    assert el._known_apply_strategy(conn, "mystery_fix") is True


def test_db_error_fails_safe_open(tmp_path):
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("transient")
    assert el._known_apply_strategy(Broken(), "mystery_fix") is True


# --- lifecycle enqueue -------------------------------------------------------

def test_enqueue_candidate_refuses_a_foreign_domain(tmp_path):
    conn = _conn(tmp_path)
    assert recipe_lifecycle.enqueue_candidate(conn, **ACQ) is False
    assert recipe_lifecycle.pending_candidates(conn) == []


def test_enqueue_candidate_still_accepts_a_signoff_strategy(tmp_path):
    conn = _conn(tmp_path)
    assert recipe_lifecycle.enqueue_candidate(conn, **SIGNOFF) is True
    assert [c["strategy"] for c in recipe_lifecycle.pending_candidates(conn)] == \
        ["density_relief"]


def test_learner_diff_never_enqueues_a_foreign_domain(tmp_path):
    conn = _conn(tmp_path)
    heur = {"generation": 1, "recipes": {
        ACQ["symptom_id"]: {ACQ["design_class"]: {ACQ["platform"]: {
            "strategies": {"acquire_exclude": {"n": 3}, "density_relief": {"n": 3}}}}}}}
    enqueued = recipe_lifecycle.diff_and_enqueue(conn, heur, prev=None)
    assert [k[3] for k in enqueued] == ["density_relief"]


def test_unrostered_keys_ignores_foreign_domains(tmp_path):
    conn = _conn(tmp_path)
    heur = {"generation": 1, "recipes": {
        ACQ["symptom_id"]: {ACQ["design_class"]: {ACQ["platform"]: {
            "strategies": {"acquire_exclude": {"n": 1}}}}}}}
    assert recipe_lifecycle.unrostered_keys(conn, heur) == []


# --- self-heal for stores that predate the filter ---------------------------

def test_park_foreign_domain_heals_a_leaked_candidate(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,1,?)",
        (ACQ["symptom_id"], ACQ["design_class"], ACQ["platform"], ACQ["strategy"],
         "candidate", "legacy_leak", "2026-07-01T00:00:00Z"))
    conn.commit()
    assert recipe_lifecycle.park_foreign_domain(conn) == 1
    assert recipe_lifecycle.pending_candidates(conn) == []
    assert recipe_lifecycle.get_status(conn, **ACQ) == "parked"
    # Parking is a lifecycle transition: the version must advance, or a trial
    # planned before the park keeps looking current (2026-07-19 audit P0-N1).
    assert recipe_lifecycle.get_status_version(conn, **ACQ) > 1


def test_park_foreign_domain_leaves_signoff_candidates_alone(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, **SIGNOFF)
    assert recipe_lifecycle.park_foreign_domain(conn) == 0
    assert len(recipe_lifecycle.pending_candidates(conn)) == 1


def test_plan_arms_creates_no_arm_for_a_leaked_acquisition_candidate(tmp_path):
    """The end-to-end acceptance condition: an acquisition disposition creates
    zero signoff A/B arms."""
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,1,?)",
        (ACQ["symptom_id"], ACQ["design_class"], ACQ["platform"], ACQ["strategy"],
         "candidate", "legacy_leak", "2026-07-01T00:00:00Z"))
    _fix_event(conn, "acquire_exclude", "synth")
    conn.commit()
    led = el.Ledger(tmp_path / "l.jsonl")
    for i in range(3):
        led.add({"design": f"d{i}", "project_path": str(tmp_path / f"d{i}"),
                 "platform": "sky130hs", "kind": "normal"})
    assert el.plan_arms_for_candidates(led, conn, repeats=1) == 0
    assert [e for e in led.entries() if e.get("kind") == "ab_arm"] == []
    assert conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0] == 0
