from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tehm.evaluation.research_inventory import (
    ResearchInventoryError,
    bind_research_inventory_adapters,
    build_research_inventory,
    verify_research_inventory,
)


def _project(corpus: Path, family: str, name: str, *, repo: str) -> Path:
    project = corpus / family / name
    (project / "rtl").mkdir(parents=True)
    (project / "rtl" / "child.v").write_text(
        "module child(input wire clk, output wire y); assign y = clk; endmodule\n",
        encoding="utf-8",
    )
    (project / "rtl" / "top.v").write_text(
        "`include \"defs.vh\"\n"
        "module top(input wire clk, output wire y); child u_child(.clk(clk), .y(y)); endmodule\n",
        encoding="utf-8",
    )
    (project / "rtl" / "defs.vh").write_text("`define WIDTH 1\n", encoding="utf-8")
    (project / "config.tcl").write_text(
        'set TOP_NAME "top"\nset CLOCK_NAME "clk"\nset clk_period 2.0\n',
        encoding="utf-8",
    )
    (project / "src_manifest.txt").write_text(
        "rtl/defs.vh\nrtl/child.v\nrtl/top.v\n", encoding="utf-8"
    )
    (project / "design_meta.json").write_text(
        json.dumps({"design": name, "top": "top", "notes": f"repo={repo}",
                    "status": "success"}),
        encoding="utf-8",
    )
    return project


def test_inventory_is_read_only_explicit_and_replayable(tmp_path: Path) -> None:
    corpus = tmp_path / "RTL"
    first = _project(corpus, "catalog", "alpha", repo="example-alpha")
    _project(corpus, "catalog", "beta", repo="example-beta")
    (first / "constraints.sdc").write_text(
        "create_clock -name clk -period 2.0 [get_ports clk]\n", encoding="utf-8"
    )
    before = {
        path.relative_to(corpus).as_posix(): path.read_bytes()
        for path in corpus.rglob("*") if path.is_file()
    }

    output = tmp_path / "inventory-a"
    result = build_research_inventory(corpus_root=corpus, output=output)

    assert result["valid"] is True
    assert result["candidate_count"] == 2
    assert result["corpus_unchanged"] is True
    after = {
        path.relative_to(corpus).as_posix(): path.read_bytes()
        for path in corpus.rglob("*") if path.is_file()
    }
    assert after == before
    inventory = json.loads((output / "inventory.json").read_text())
    assert inventory["authority"]["stub_generation"] is False
    assert inventory["preflight_shortlist"]["selected_count"] == 2
    shortlist = json.loads((output / "preflight-shortlist.json").read_text())
    assert shortlist["selected_count"] == 2
    assert all(row["status"] == "PROPOSED_NOT_RUN" for row in shortlist["selected"])
    pilot_entry = json.loads((output / "pilot-entry-proposal.json").read_text())
    assert pilot_entry["status"] == "BLOCKED_PENDING_OFFICIAL_CONTROL"
    assert pilot_entry["official_control"] is None
    alpha = json.loads((output / "design-manifests" / "alpha.json").read_text())
    assert alpha["compilation"]["top_module"] == "top"
    assert alpha["compilation"]["top_authority"] == "explicit_config"
    assert alpha["compilation"]["filelist_authority"] == "explicit:src_manifest.txt"
    assert alpha["identity"]["origin"]["lineage_group"] == "declared-repo:example-alpha"
    assert alpha["identity"]["origin"]["catalog_bucket_is_not_lineage"] is True
    assert alpha["readiness"]["status"] == "NEEDS_ADAPTER"
    assert alpha["readiness"]["ready_flow"] is False
    assert alpha["readiness"]["prior_flow_status_authority"] == "historical_metadata_unverified"
    assert alpha["parser_binding"]["adapter"] == "rtl-acquire-discovery-read-only"
    assert verify_research_inventory(output)["valid"] is True

    second = tmp_path / "inventory-b"
    build_research_inventory(corpus_root=corpus, output=second)
    assert (output / "inventory.json").read_bytes() == (second / "inventory.json").read_bytes()
    assert (output / "design-manifests" / "alpha.json").read_bytes() == (
        second / "design-manifests" / "alpha.json"
    ).read_bytes()


def test_inventory_does_not_follow_escaping_symlink(tmp_path: Path) -> None:
    corpus = tmp_path / "RTL"
    project = _project(corpus, "catalog", "alpha", repo="example-alpha")
    _project(corpus, "catalog", "beta", repo="example-beta")
    secret = tmp_path / "outside.v"
    secret.write_text("module outside; endmodule\n", encoding="utf-8")
    (project / "rtl" / "escape.v").symlink_to(secret)

    output = tmp_path / "inventory"
    build_research_inventory(corpus_root=corpus, output=output)
    alpha = json.loads((output / "design-manifests" / "alpha.json").read_text())
    assert alpha["readiness"]["status"] == "UNSUPPORTED_CURRENT_PROFILE"
    assert "symlink_escapes_corpus" in alpha["readiness"]["reasons"]
    assert all(row["path"] != "rtl/escape.v" for row in alpha["source_files"])
    exclusions = json.loads((output / "exclusions.json").read_text())
    assert any(row["reason"] == "symlink_escapes_corpus_not_followed"
               for row in exclusions["entries"])
    assert any(row["reason"] == "unsupported_current_profile"
               and row.get("design_id") == "alpha" for row in exclusions["entries"])


def test_inventory_rejects_source_output_and_detects_drift(tmp_path: Path) -> None:
    corpus = tmp_path / "RTL"
    project = _project(corpus, "catalog", "alpha", repo="example-alpha")
    _project(corpus, "catalog", "beta", repo="example-beta")
    with pytest.raises(ResearchInventoryError, match="outside the corpus"):
        build_research_inventory(corpus_root=corpus, output=corpus / "inventory")

    output = tmp_path / "inventory"
    build_research_inventory(corpus_root=corpus, output=output)
    (project / "rtl" / "top.v").write_text("module top; endmodule\n", encoding="utf-8")
    with pytest.raises(ResearchInventoryError, match="source corpus drifted"):
        verify_research_inventory(output)


def test_inventory_detects_frozen_artifact_tamper(tmp_path: Path) -> None:
    corpus = tmp_path / "RTL"
    _project(corpus, "catalog", "alpha", repo="example-alpha")
    _project(corpus, "catalog", "beta", repo="example-beta")
    output = tmp_path / "inventory"
    build_research_inventory(corpus_root=corpus, output=output)
    candidates = output / "candidates.csv"
    candidates.write_text(candidates.read_text(encoding="utf-8") + "tamper\n",
                          encoding="utf-8")
    with pytest.raises(ResearchInventoryError, match="artifact drifted"):
        verify_research_inventory(output)


def test_explicit_adapter_binds_clean_authority_without_source_changes(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "orfs"
    corpus = authority / "flow/designs/src"
    _project(corpus, "catalog", "alpha", repo="example-alpha")
    support = authority / "flow/designs/platform/alpha"
    support.mkdir(parents=True)
    (support / "config.mk").write_text("DESIGN_NAME = top\n", encoding="utf-8")
    (support / "constraint.sdc").write_text(
        "create_clock -name clk -period 2.0 [get_ports clk]\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(authority)], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.name", "Research Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(authority), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(
        corpus_root=corpus, output=source_inventory
    )
    spec = tmp_path / "adapter.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v1",
        "adapter_id": "test-official-v1",
        "source_inventory_digest": source_result["inventory_digest"],
        "authority_checkout": {
            "git_head": head,
            "require_clean": True,
            "source_subtree": "flow/designs/src",
        },
        "designs": [{
            "design_id": "alpha",
            "role": "official_control",
            "top_module": "top",
            "ordered_filelist": ["rtl/defs.vh", "rtl/child.v", "rtl/top.v"],
            "include_dirs": ["rtl"],
            "defines": {},
            "top_parameters": {},
            "support_files": [
                "flow/designs/platform/alpha/config.mk",
                "flow/designs/platform/alpha/constraint.sdc",
            ],
            "flow_binding": {"platform": "test"},
        }],
    }), encoding="utf-8")
    output = tmp_path / "adapted"
    result = bind_research_inventory_adapters(
        inventory=source_inventory,
        adapter_spec=spec,
        authority_root=authority,
        output=output,
    )

    assert result["valid"] is True
    assert result["candidate_count"] == 1
    manifest = json.loads(
        (output / "design-manifests/alpha.json").read_text(encoding="utf-8")
    )
    assert manifest["compilation"]["top_authority"] == "explicit_adapter_spec"
    assert manifest["compilation"]["filelist_authority"] == "explicit:adapter-spec.json"
    assert manifest["identity"]["git"]["git_head"] == head
    assert manifest["adapter_binding"]["logic_changes"] == []
    assert manifest["adapter_binding"]["stub_generated"] is False
    assert len(manifest["adapter_binding"]["support_files"]) == 2
    assert verify_research_inventory(output)["valid"] is True


def test_explicit_adapter_rejects_dirty_or_unbound_authority(tmp_path: Path) -> None:
    authority = tmp_path / "orfs"
    corpus = authority / "flow/designs/src"
    _project(corpus, "catalog", "alpha", repo="example-alpha")
    subprocess.run(["git", "init", "-q", str(authority)], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(authority), "config", "user.name", "Research Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(authority), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(corpus_root=corpus, output=source_inventory)
    spec = tmp_path / "adapter.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v1",
        "adapter_id": "test-official-v1",
        "source_inventory_digest": source_result["inventory_digest"],
        "authority_checkout": {
            "git_head": head,
            "require_clean": True,
            "source_subtree": "flow/designs/src",
        },
        "designs": [{
            "design_id": "alpha", "role": "official_control",
            "top_module": "top", "ordered_filelist": ["rtl/top.v"],
        }],
    }), encoding="utf-8")
    (authority / "dirty.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(ResearchInventoryError, match="observed clean"):
        bind_research_inventory_adapters(
            inventory=source_inventory, adapter_spec=spec,
            authority_root=authority, output=tmp_path / "adapted",
        )


def test_v2_adapter_separates_snapshot_source_from_git_support(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "RTL"
    _project(corpus, "external", "alpha", repo="independent-alpha")
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(
        corpus_root=corpus, output=source_inventory
    )
    source_payload = json.loads(
        (source_inventory / "inventory.json").read_text(encoding="utf-8")
    )

    support_authority = tmp_path / "orfs"
    support = support_authority / "flow/designs/platform/template"
    support.mkdir(parents=True)
    (support / "config.mk").write_text("DESIGN_NAME = template\n", encoding="utf-8")
    (support / "constraint.sdc").write_text(
        "current_design template\n"
        "set clk_port_name clk\n"
        "set clk_period 2.0\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(support_authority)], check=True)
    subprocess.run(
        ["git", "-C", str(support_authority), "config", "user.email",
         "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(support_authority), "config", "user.name",
         "Research Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(support_authority), "add", "."], check=True
    )
    subprocess.run(
        ["git", "-C", str(support_authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(support_authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    spec = tmp_path / "adapter-v2.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v2",
        "adapter_id": "test-external-v2",
        "source_inventory_digest": source_result["inventory_digest"],
        "source_authority": {
            "kind": "inventory_snapshot",
            "corpus_root": str(corpus.resolve()),
            "inventory_digest": source_result["inventory_digest"],
            "entries_digest": source_payload["corpus_snapshot_after"]["entries_digest"],
        },
        "support_authority": {
            "kind": "git_checkout",
            "git_head": head,
            "require_clean": True,
        },
        "designs": [{
            "design_id": "alpha",
            "role": "external_server_design",
            "top_module": "top",
            "ordered_filelist": ["rtl/defs.vh", "rtl/child.v", "rtl/top.v"],
            "include_dirs": ["rtl"],
            "support_files": [
                "flow/designs/platform/template/config.mk",
                "flow/designs/platform/template/constraint.sdc",
            ],
            "flow_binding": {"platform": "test"},
        }],
    }), encoding="utf-8")
    output = tmp_path / "adapted"
    result = bind_research_inventory_adapters(
        inventory=source_inventory,
        adapter_spec=spec,
        authority_root=support_authority,
        output=output,
    )

    assert result["valid"] is True
    manifest = json.loads(
        (output / "design-manifests/alpha.json").read_text(encoding="utf-8")
    )
    assert manifest["identity"]["git"] is None
    assert manifest["identity"]["origin"]["lineage_status"] == (
        "declared_metadata_unverified"
    )
    binding = manifest["adapter_binding"]
    assert binding["source_authority"]["kind"] == "inventory_snapshot"
    assert binding["source_authority"]["entries_digest"] == (
        source_payload["corpus_snapshot_after"]["entries_digest"]
    )
    assert binding["authority_checkout"]["git_head"] == head


def test_v2_adapter_rejects_wrong_source_snapshot(tmp_path: Path) -> None:
    corpus = tmp_path / "RTL"
    _project(corpus, "external", "alpha", repo="independent-alpha")
    source_inventory = tmp_path / "source-inventory"
    source_result = build_research_inventory(
        corpus_root=corpus, output=source_inventory
    )
    support_authority = tmp_path / "orfs"
    support_authority.mkdir()
    subprocess.run(["git", "init", "-q", str(support_authority)], check=True)
    subprocess.run(
        ["git", "-C", str(support_authority), "config", "user.email",
         "test@example.invalid"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(support_authority), "config", "user.name", "Test"],
        check=True,
    )
    (support_authority / "tracked").write_text("support\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(support_authority), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(support_authority), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(support_authority), "rev-parse", "HEAD"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    spec = tmp_path / "adapter-v2.json"
    spec.write_text(json.dumps({
        "schema": "tehm-research-inventory-adapter-spec-v2",
        "adapter_id": "bad-v2",
        "source_inventory_digest": source_result["inventory_digest"],
        "source_authority": {
            "kind": "inventory_snapshot",
            "corpus_root": str(corpus.resolve()),
            "inventory_digest": source_result["inventory_digest"],
            "entries_digest": "sha256:wrong",
        },
        "support_authority": {
            "kind": "git_checkout", "git_head": head, "require_clean": True,
        },
        "designs": [{
            "design_id": "alpha", "top_module": "top",
            "ordered_filelist": ["rtl/top.v"],
        }],
    }), encoding="utf-8")
    with pytest.raises(ResearchInventoryError, match="source inventory authority"):
        bind_research_inventory_adapters(
            inventory=source_inventory,
            adapter_spec=spec,
            authority_root=support_authority,
            output=tmp_path / "adapted",
        )
