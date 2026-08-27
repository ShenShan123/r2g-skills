from __future__ import annotations

from scripts.promote_orfs_evidence import promote


def test_promote_orfs_evidence_skips_reproducible_run_trees(tmp_path):
    scratch = tmp_path / "scratch"
    evidence = tmp_path / "evidence"
    (scratch / "reports").mkdir(parents=True)
    (scratch / "backend" / "RUN_1" / "results").mkdir(parents=True)
    (scratch / "backend" / "RUN_1" / "final").mkdir(parents=True)
    (scratch / "campaign_manifest.json").write_text("{}\n")
    (scratch / "add_designs_report.json").write_text("{}\n")
    (scratch / "reports" / "summary.json").write_text("{}\n")
    (scratch / "backend" / "RUN_1" / "stage_log.jsonl").write_text("{}\n")
    (scratch / "backend" / "RUN_1" / "results" / "huge.odb").write_text("not copied\n")
    (scratch / "backend" / "RUN_1" / "results" / "reports").mkdir()
    (scratch / "backend" / "RUN_1" / "results" / "reports" / "tool.json").write_text("not copied\n")
    (scratch / "cases" / ".orfs-work" / "reports").mkdir(parents=True)
    (scratch / "cases" / ".orfs-work" / "reports" / "preview.png").write_bytes(b"not copied\n")
    (scratch / "backend" / "RUN_1" / "final" / "6_final.def").write_text("VERSION 5.8 ;\n")
    result = promote(scratch, evidence)
    assert result["copied_count"] == 5
    assert (evidence / "reports" / "summary.json").is_file()
    assert (evidence / "final" / "1" / "6_final.def").is_file()
    assert (evidence / "receipts" / "1" / "stage_log.jsonl").is_file()
    assert not list(evidence.rglob("RUN_*"))
    assert not (evidence / "backend" / "RUN_1" / "results" / "huge.odb").exists()
    assert not (evidence / "backend" / "RUN_1" / "results" / "reports" / "tool.json").exists()
    assert not (evidence / "cases" / ".orfs-work" / "reports" / "preview.png").exists()
    assert (scratch / "backend" / "RUN_1" / "results" / "huge.odb").is_file()
