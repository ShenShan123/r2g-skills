"""_safe_process must record the exception MESSAGE, not just its type.

Root cause (2026-06-29 wbscope crash): the parallel worker guard `_safe_process`
caught any exception from a design and escalated it as `reason=worker_exc:<Type>`
WITHOUT the exception message or traceback. Four designs (wbscope_wishbone/_avalon/
_axil, zipcpu_wbdmac) escalated `worker_exc:ValueError` and, by the time the crash was
investigated, their on-disk state had moved on — so the root-cause line was unfindable.
A worker crash with a swallowed traceback is undiagnosable: the loop can't learn from a
failure it can't see. Fix: print the full traceback to the wave log (stderr) and stamp
the one-line message onto the ledger `note`; keep `reason=worker_exc:<Type>` stable for
triage. See references/failure-patterns.md ("worker_exc — undiagnosable worker crash").
"""
import engineer_loop as el


def test_safe_process_stamps_message_and_keeps_reason(tmp_path, monkeypatch, capsys):
    led = el.Ledger(tmp_path / "l.jsonl")
    entry = {"design": "wbscope_wishbone",
             "project_path": str(tmp_path / "wbscope_wishbone"),
             "platform": "nangate45"}
    led.add(entry)                          # design exists before the worker runs (as in prod)

    def boom(led_, ent, db):
        raise ValueError("inferred memory 32768 bits could not be parsed")
    monkeypatch.setattr(el, "_drain_arm", boom)

    # must NOT propagate — a crash in one design never aborts the batch
    el._safe_process(led, entry)

    e = {x["design"]: x for x in led.entries()}[entry["design"]]
    assert e["state"] == "escalated"
    # reason key stays stable (triage/honesty bucketing keys off it)
    assert e["reason"] == "worker_exc:ValueError"
    # the NEW behavior: the exception MESSAGE is captured (was swallowed before)
    assert "note" in e, "worker crash escalated with no diagnostic note (traceback swallowed)"
    assert "ValueError" in e["note"] and "32768 bits" in e["note"]

    # and the full traceback reached the log so the operator can find the line
    err = capsys.readouterr().err
    assert "wbscope_wishbone" in err and "Traceback" in err
