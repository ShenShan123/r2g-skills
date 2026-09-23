"""Failed-extractor quarantine + missing-label degradation
(failure-patterns.md #52; 2026-07-19 audit P0-N4).

Fail-soft is deliberate in run_labels.sh: one dead extractor must degrade ONE
label column, not abort the other six. But it was also fail-SILENT. The
extractors wrote in place, so a FAILED extractor left the previous run's CSV at
the canonical path, and the unconditional stats roll-up then stamped a fresh
completion marker over it:

    selected DEF:        2026-07-19 10:46:50
    timing_features.csv: 2026-07-14 13:13:35   <- extractor had just FAILED
    labels_stats.json:   2026-07-19 10:47:10   <- new completion marker

A stale CSV is perfectly well-formed, so it reads 'ok' at every gate. And when
NO old CSV survived, the graph builder and verifier died with an uncaught
FileNotFoundError instead of producing a structured manifest.

Two halves, tested here:
  * run_soft quarantines its targets BEFORE launching -> a failure leaves the
    path ABSENT, which compute_label_stats already calls 'skipped';
  * graph_lib degrades an absent label file to an empty frame + an explicit
    'missing' label_health entry, so the manifest says ok_with_label_gaps.
"""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap

import pytest

# graph_lib imports pandas at module level. Only the three tests that call it
# need it, so only those skip when this interpreter lacks it (the graph venv,
# R2G_GRAPH_PYTHON, has it).
_GL_MISSING = next((m for m in ("pandas",)
                    if importlib.util.find_spec(m) is None), None)
needs_graph_lib = pytest.mark.skipif(
    _GL_MISSING is not None,
    reason=f"graph_lib needs {_GL_MISSING}, missing from this interpreter "
           "(run under R2G_GRAPH_PYTHON)")

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FLOW = os.path.join(_SKILL, "scripts", "flow")
_LABELS_SRC = os.path.join(_SKILL, "scripts", "extract", "labels")
_RUN_LABELS = os.path.join(_FLOW, "run_labels.sh")

sys.path.insert(0, os.path.join(_SKILL, "scripts", "extract", "graph"))
if _GL_MISSING is None:
    import pandas as pd
    import graph_lib as gl  # noqa: E402


def _run_soft(tmp_path, *, targets, succeed, preexisting=None):
    """Source the REAL run_soft out of run_labels.sh and drive it, so the test
    binds to shipped code rather than a copy that can drift."""
    labels = tmp_path / "labels"
    labels.mkdir(exist_ok=True)
    for name, body in (preexisting or {}).items():
        (labels / name).write_text(body, encoding="utf-8")
    fn = subprocess.run(
        ["awk", "/^run_soft\\(\\)/,/^}$/", _RUN_LABELS],
        capture_output=True, text=True, check=True).stdout
    assert "quarantine" in fn.lower() or "stale" in fn, \
        "run_soft in run_labels.sh no longer quarantines its targets"
    cmd = "true" if succeed else "false"
    tgt = " ".join(str(labels / t) for t in targets)
    script = textwrap.dedent(f"""
        set -uo pipefail
        LABELS_DIR={labels!s}
        LABEL_TIMEOUT=30
        {fn}
        run_soft probe "{tgt}" {cmd}
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    return labels, r


def test_failed_extractor_quarantines_the_stale_csv(tmp_path):
    """The report's reproduction: a stale timing CSV must NOT stay at the
    canonical path after its extractor fails."""
    labels, r = _run_soft(
        tmp_path, targets=["timing_features.csv"], succeed=False,
        preexisting={"timing_features.csv": "Design,Cell,label\nd,c1,0.5\n"})
    assert not (labels / "timing_features.csv").exists(), \
        "stale CSV was republished as current"
    assert (labels / "timing_features.csv.stale").exists(), \
        "stale CSV was destroyed rather than quarantined"
    assert "quarantined" in r.stderr and "FAILED" in r.stderr


def test_successful_extractor_clears_the_quarantine(tmp_path):
    """A success must leave no .stale residue to confuse the next operator."""
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "wirelength.csv").write_text("old\n", encoding="utf-8")
    fn = subprocess.run(["awk", "/^run_soft\\(\\)/,/^}$/", _RUN_LABELS],
                        capture_output=True, text=True, check=True).stdout
    assert ".stale" in fn, "run_soft no longer quarantines — this test would " \
                           "otherwise pass vacuously against the unfixed script"
    # `touch` recreates the target, standing in for a real extractor.
    script = textwrap.dedent(f"""
        set -uo pipefail
        LABELS_DIR={labels!s}
        LABEL_TIMEOUT=30
        {fn}
        run_soft probe "{labels / 'wirelength.csv'}" touch {labels / 'wirelength.csv'}
    """)
    subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert (labels / "wirelength.csv").exists()
    assert not (labels / "wirelength.csv.stale").exists()


def test_quarantine_handles_a_first_ever_run(tmp_path):
    """No prior output: nothing to quarantine, and no spurious .stale file."""
    labels, r = _run_soft(tmp_path, targets=["ir_drop.csv"], succeed=False)
    assert not (labels / "ir_drop.csv").exists()
    assert not (labels / "ir_drop.csv.stale").exists()


def test_multi_target_extractor_quarantines_every_target(tmp_path):
    """extract_rc.py owns three CSVs — a partial quarantine would leave one
    stale RC label masquerading as current."""
    pre = {n: "Design,Net,label\nd,n1,1.0\n"
           for n in ("net_ground_cap.csv", "coupling_cap.csv", "equiv_res.csv")}
    labels, _ = _run_soft(tmp_path, targets=list(pre), succeed=False,
                          preexisting=pre)
    for n in pre:
        assert not (labels / n).exists(), f"{n} republished as current"
        assert (labels / f"{n}.stale").exists()


def test_every_run_soft_call_site_declares_its_targets():
    """A call site that forgets its targets silently re-opens the hole."""
    src = open(_RUN_LABELS, encoding="utf-8").read()
    calls = [ln.strip() for ln in src.splitlines()
             if ln.strip().startswith("run_soft ") and "()" not in ln]
    assert len(calls) >= 5, f"expected the 5+ extractor call sites, saw {calls}"
    for call in calls:
        assert ".csv" in call, f"run_soft call declares no target CSV: {call}"


def test_stats_gate_calls_a_quarantined_label_skipped(tmp_path):
    """End-to-end honesty: after quarantine the roll-up must NOT read 'ok'."""
    spec = importlib.util.spec_from_file_location(
        "compute_label_stats", os.path.join(_LABELS_SRC, "compute_label_stats.py"))
    cls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cls)
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "wirelength.csv").write_text(
        "Design,Net,label,WireLength_um\nd,n1,1.0,10.0\n", encoding="utf-8")
    out = tmp_path / "labels_stats.json"
    subprocess.run([sys.executable, os.path.join(_LABELS_SRC, "compute_label_stats.py"),
                    str(labels), str(out), "d", "nangate45"],
                   capture_output=True, text=True, check=True)
    stats = json.loads(out.read_text())
    assert stats["labels"]["timing"]["status"] == "skipped"
    assert stats["labels"]["wirelength"]["status"] == "ok"


# ---- graph-side degradation ------------------------------------------------

@needs_graph_lib
def test_missing_label_file_yields_empty_frame_not_traceback(tmp_path):
    """Was: uncaught FileNotFoundError out of the graph builder AND verifier,
    which reads as a graph/design defect rather than an upstream tool failure."""
    df = gl.load_label_df(str(tmp_path), "timing_features.csv")
    assert isinstance(df, pd.DataFrame) and df.empty


@needs_graph_lib
def test_label_cache_survives_a_missing_family(tmp_path):
    cache = gl.load_label_cache(str(tmp_path))
    assert cache and all(d.empty for d in cache.values())


@needs_graph_lib
def test_label_health_names_a_missing_file_explicitly(tmp_path):
    """'no Design column — raw/unprocessed csv?' would send an operator hunting a
    format bug in a file that was never written."""
    cache = gl.load_label_cache(str(tmp_path))
    health = gl.label_health(cache, "d", str(tmp_path))
    assert health, "label_health returned nothing"
    for name, h in health.items():
        assert h["status"] == "missing", (name, h)
        assert "extractor failed or never ran" in h["reason"]
    assert not all(h["status"] == "ok" for h in health.values()), \
        "a missing label set must not leave the manifest status 'ok'"
