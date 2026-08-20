import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_expansion_round.py"
SPEC = importlib.util.spec_from_file_location("rtl_expander_round", SCRIPT)
rtl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(rtl)


class TopRecoveryTests(unittest.TestCase):
    def test_project_boundaries_prevent_cross_project_module_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "alpha").mkdir()
            (repo / "beta").mkdir()
            (repo / "alpha" / "design.v").write_text(
                "module helper(input a, output y); assign y=a; endmodule\n"
                "module alpha_top(input a, output y); helper u(a,y); endmodule\n"
                "module alpha_tb; reg a; initial begin #5 $finish; end endmodule\n"
            )
            (repo / "beta" / "design.v").write_text(
                "module helper(input a, output y); assign y=~a; endmodule\n"
                "module beta_top(input a, output y); helper u(a,y); endmodule\n"
            )
            files = rtl.source_files(repo, 100)
            groups = rtl.project_groups(repo, files)
            self.assertEqual(set(groups), {"alpha", "beta"})
            alpha_units, alpha_edges, _, alpha_duplicates = rtl.parse_design_units(groups["alpha"])
            beta_units, beta_edges, _, beta_duplicates = rtl.parse_design_units(groups["beta"])
            self.assertFalse(alpha_duplicates)
            self.assertFalse(beta_duplicates)
            self.assertEqual(alpha_edges["alpha_top"], {"helper"})
            self.assertEqual(beta_edges["beta_top"], {"helper"})

    def test_manifest_top_wins_and_testbench_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            project = repo / "demo"
            project.mkdir()
            (project / "rtl.v").write_text(
                "module leaf(input a, output y); assign y=a; endmodule\n"
                "module selected_top(input a, output y); leaf u(a,y); endmodule\n"
                "module plausible_core(input a, output y); assign y=a; endmodule\n"
                "module testbench; reg a; wire y; selected_top dut(a,y); initial begin #10 $finish; end endmodule\n"
            )
            (project / "demo.core").write_text("toplevel: selected_top\n")
            files = rtl.source_files(repo, 100)
            units, edges, _, _ = rtl.parse_design_units(files)
            candidates = rtl.candidate_tops(repo, "demo", units, edges, 10)
            self.assertEqual(candidates[0]["top"], "selected_top")
            self.assertIn("STATIC_MANIFEST", candidates[0]["evidence"])
            self.assertNotIn("testbench", {item["top"] for item in candidates})

    def test_duplicate_definitions_are_not_arbitrarily_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "a.v").write_text("module duplicate(input a, output y); assign y=a; endmodule\n")
            (repo / "b.v").write_text("module duplicate(input a, output y); assign y=~a; endmodule\n")
            units, _, _, duplicates = rtl.parse_design_units(rtl.source_files(repo, 100))
            self.assertIn("duplicate", duplicates)
            self.assertNotIn("duplicate", units)

    def test_weak_module_roots_do_not_become_design_tops(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            project = repo / "large_project"
            project.mkdir()
            (project / "stages.v").write_text(
                "module decode(input a, output y); assign y=a; endmodule\n"
                "module fetch(input a, output y); assign y=a; endmodule\n"
                "module regfile(input a, output y); assign y=a; endmodule\n"
            )
            units, edges, _, _ = rtl.parse_design_units(rtl.source_files(repo, 100))
            candidates = rtl.candidate_tops(repo, "large_project", units, edges, 10)
            self.assertEqual(candidates, [])


class SchemaPolicyTests(unittest.TestCase):
    @staticmethod
    def canonical_plan(plan):
        for component in plan["components"]:
            groups = sorted(set(map(str, component["old_split_groups"])))
            splits = sorted(set(map(str, component["old_splits"])))
            target = component.get("target_split") or (
                "test" if "test" in splits else "val"
            )
            component_hash = hashlib.sha256(
                (("\n".join(groups)) + "\n").encode()
            ).hexdigest()
            authorization = {
                "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
                "canonical_component_members": groups,
                "input_splits": splits,
                "target_split": target,
            }
            component.update({
                "component_id": component_hash,
                "canonical_component_hash": component_hash,
                "canonical_component_members": groups,
                "old_split_groups": groups,
                "old_splits": splits,
                "input_splits": splits,
                "target_split": target,
                "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
                "authorized_component_hash": hashlib.sha256(
                    json.dumps(
                        authorization, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "component_boundary_identity_edges": 0,
                "component_member_loss": 0,
                "component_split_set_exactly_known": True,
            })
        plan.update({
            "component_normalization": "GLOBAL_MAXIMAL_TRANSITIVE_SPLITGROUP_COMPONENT_V1",
            "reconciliation_plan_components_pairwise_disjoint": True,
            "component_overlap_count": 0,
            "component_boundary_identity_edges": 0,
            "component_member_loss": 0,
        })
        material = dict(plan)
        material.pop("plan_sha256", None)
        plan["plan_sha256"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return plan

    def design(self, design_id, repo_url):
        source_hash = rtl.digest(design_id.encode())
        return {
            "design_id": design_id,
            "family_id": "provisional",
            "revision_id": rtl.stable_id("rev", repo_url),
            "identity": {"repository_name": design_id, "project_key": design_id},
            "provenance": {"repository_url": repo_url, "commit_sha": "1" * 40},
            "build": {"top_module": "fifo_top", "dependency_modules": ["fifo_top"]},
            "source": {"source_units": [{"path": f"rtl/{design_id}.v", "language": "verilog", "sha256": source_hash}]},
            "dedup": {
                "source_hash": "a" * 64,
                "normalized_hash": "b" * 64,
                "hierarchy_hash": "c" * 64,
                "generic_netlist_hash": "d" * 64,
            },
            "quality": {"training_tier": "TRAINING_SILVER", "quality_flags": []},
        }

    def test_same_family_gets_one_frozen_split(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=False, split_seed="seed", train_percent=80, val_percent=10)
            designs = {
                "d1": self.design("d1", "https://example.com/org/upstream.git"),
                "d2": self.design("d2", "https://mirror.example/fork/copy.git"),
            }
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual(designs["d1"]["family_id"], designs["d2"]["family_id"])
            self.assertEqual(designs["d1"]["split"], designs["d2"]["split"])
            original = designs["d1"]["split"]
            args.split_seed = "changed-seed"
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual(designs["d1"]["split"], original)

    def test_organization_aware_split_uses_family_org_components(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=True, split_seed="seed", train_percent=80, val_percent=10)
            d1 = self.design("d1", "https://forge.example/org1/upstream.git")
            d2 = self.design("d2", "https://forge.example/org2/fork.git")
            d3 = self.design("d3", "https://forge.example/org2/other.git")
            d3["build"]["top_module"] = "other_top"
            d3["dedup"] = {"source_hash": "e" * 64, "normalized_hash": "f" * 64, "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64}
            designs = {"d1": d1, "d2": d2, "d3": d3}
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual(len({record["split_group_id"] for record in designs.values()}), 1)
            self.assertEqual(len({record["split"] for record in designs.values()}), 1)

    def test_shared_source_closure_groups_distinct_families(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=False, split_seed="seed", train_percent=80, val_percent=10)
            cpu = self.design("cpu", "https://forge.example/org/cpu.git")
            soc = self.design("soc", "https://forge.example/org/soc.git")
            soc["build"]["top_module"] = "soc_top"
            soc["dedup"] = {"source_hash": "e" * 64, "normalized_hash": "f" * 64, "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64}
            shared_hash = "9" * 64
            cpu["source"]["source_units"].append({"path": "rtl/shared.v", "language": "verilog", "sha256": shared_hash})
            soc["source"]["source_units"].append({"path": "ip/cpu/shared.v", "language": "verilog", "sha256": shared_hash})
            designs = {"cpu": cpu, "soc": soc}
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertNotEqual(cpu["family_id"], soc["family_id"])
            self.assertEqual(cpu["split_group_id"], soc["split_group_id"])
            self.assertIn("source_closure", cpu["split_group_evidence"])

    def test_ancestor_descendant_hierarchy_groups_distinct_families(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=False, split_seed="seed", train_percent=80, val_percent=10)
            cpu = self.design("cpu", "https://forge.example/org/soc.git")
            cpu["build"] = {"top_module": "cpu_core", "dependency_modules": ["cpu_core"]}
            soc = self.design("soc", "https://forge.example/org/soc.git")
            soc["build"] = {"top_module": "soc_top", "dependency_modules": ["soc_top", "cpu_core"]}
            soc["dedup"] = {"source_hash": "e" * 64, "normalized_hash": "f" * 64, "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64}
            designs = {"cpu": cpu, "soc": soc}
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertNotEqual(cpu["family_id"], soc["family_id"])
            self.assertEqual(cpu["split_group_id"], soc["split_group_id"])
            self.assertIn("hierarchy_top", soc["split_group_evidence"])

    def test_tightly_coupled_project_target_groups_distinct_families(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=False, split_seed="seed", train_percent=80, val_percent=10)
            left = self.design("left", "https://forge.example/org/soc.git")
            right = self.design("right", "https://forge.example/org/soc.git")
            left["identity"]["project_key"] = right["identity"]["project_key"] = "soc_target"
            right["build"]["top_module"] = "right_top"
            right["dedup"] = {"source_hash": "e" * 64, "normalized_hash": "f" * 64, "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64}
            designs = {"left": left, "right": right}
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertNotEqual(left["family_id"], right["family_id"])
            self.assertEqual(left["split_group_id"], right["split_group_id"])
            self.assertIn("project_target", left["split_group_evidence"])

    def test_frozen_cross_split_component_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(organization_aware_split=False, split_seed="seed", train_percent=80, val_percent=10)
            left = self.design("left", "https://forge.example/org/left.git")
            right = self.design("right", "https://forge.example/org/right.git")
            right["build"]["top_module"] = "right_top"
            right["dedup"] = {"source_hash": "e" * 64, "normalized_hash": "f" * 64, "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64}
            designs = {"left": left, "right": right}
            rtl.assign_families_and_splits(designs, corpus, args)
            assignments = rtl.load_jsonl(corpus / "manifests" / "split_assignments.jsonl", "split_group_id")
            groups = sorted(assignments)
            assignments[groups[0]]["split"] = "train"
            assignments[groups[1]]["split"] = "test"
            rtl.write_jsonl(corpus / "manifests" / "split_assignments.jsonl", assignments.values())
            shared_hash = "8" * 64
            left["source"]["source_units"].append({"path": "shared.v", "language": "verilog", "sha256": shared_hash})
            right["source"]["source_units"].append({"path": "shared.v", "language": "verilog", "sha256": shared_hash})
            with self.assertRaisesRegex(RuntimeError, "frozen split conflict"):
                rtl.assign_families_and_splits(designs, corpus, args)

    def test_versioned_train_val_reconciliation_moves_full_component_to_val(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(
                organization_aware_split=False, split_seed="seed",
                train_percent=80, val_percent=10, split_reconciliation_plan=None,
            )
            left = self.design("left", "https://forge.example/org/left.git")
            right = self.design("right", "https://forge.example/org/right.git")
            right["build"]["top_module"] = "right_top"
            right["dedup"] = {
                "source_hash": "e" * 64, "normalized_hash": "f" * 64,
                "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64,
            }
            designs = {"left": left, "right": right}
            rtl.assign_families_and_splits(designs, corpus, args)
            assignments = rtl.load_jsonl(
                corpus / "manifests/split_assignments.jsonl", "split_group_id"
            )
            groups = sorted(assignments)
            assignments[groups[0]]["split"] = "train"
            assignments[groups[1]]["split"] = "val"
            rtl.write_jsonl(
                corpus / "manifests/split_assignments.jsonl", assignments.values()
            )
            round_id = "p2r_test_reconcile"
            round_dir = corpus / "quality/phase2/rounds" / round_id
            round_dir.mkdir(parents=True)
            cohort_path = round_dir / "cohort_lock.json"
            cohort_path.write_text(json.dumps({"acquired_revision_count": 2}) + "\n")
            cohort_hash = hashlib.sha256(cohort_path.read_bytes()).hexdigest()
            (round_dir / "cohort_lock.admission.json").write_text(json.dumps({
                "schema": "rtl_immutable_artifact_admission_v1",
                "object_id": "cohort_lock.json", "sha256": cohort_hash,
                "size": cohort_path.stat().st_size, "rehash_required": False,
            }) + "\n")
            plan_path = round_dir / "split_reconciliation_plan.json"
            plan_path.write_text(json.dumps(self.canonical_plan({
                "schema": rtl.SPLIT_RECONCILIATION_SCHEMA,
                "round_id": round_id,
                "split_epoch": "phase2_10k_reconciled_v1",
                "policy": "TRAIN_VAL_COMPONENT_TO_VAL",
                "cohort_revision_count": 2,
                "cohort_lock_sha256": cohort_hash,
                "components": [{
                    "old_split_groups": groups,
                    "old_splits": ["train", "val"],
                }],
            })))
            args.split_reconciliation_plan = plan_path
            shared_hash = "8" * 64
            left["source"]["source_units"].append(
                {"path": "shared.v", "language": "verilog", "sha256": shared_hash}
            )
            right["source"]["source_units"].append(
                {"path": "shared.v", "language": "verilog", "sha256": shared_hash}
            )
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual({record["split"] for record in designs.values()}, {"val"})
            self.assertEqual(len({record["split_group_id"] for record in designs.values()}), 1)
            reconciled_group = left["split_group_id"]
            self.assertNotIn(reconciled_group, groups)
            assignments = rtl.load_jsonl(
                corpus / "manifests/split_assignments.jsonl", "split_group_id"
            )
            self.assertEqual(assignments[reconciled_group]["merged_from"], groups)
            self.assertEqual(assignments[reconciled_group]["split_epoch"], "phase2_10k_reconciled_v1")
            for group in groups:
                self.assertEqual(assignments[group]["superseded_by"], reconciled_group)
                self.assertIn("supersession_lineage", assignments[group])
            reconciliations = rtl.load_jsonl(
                corpus / "manifests/split_reconciliations.jsonl", "reconciliation_id"
            )
            self.assertEqual(len(reconciliations), 1)
            reconciliation = next(iter(reconciliations.values()))
            self.assertEqual(reconciliation["old_splits"], ["train", "val"])
            self.assertEqual(reconciliation["new_split"], "val")
            self.assertEqual(
                reconciliation["new_canonical_split_group_id"], reconciled_group
            )
            self.assertEqual(
                reconciliation["affected_design_ids"], ["left", "right"]
            )
            self.assertEqual(
                reconciliation["superseded_by_lineage"],
                {group: reconciled_group for group in groups},
            )
            self.assertTrue(reconciliation["closure_evidence"])

    def test_test_boundary_conflict_requires_profile_transition_and_promotes_to_test(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            args = SimpleNamespace(
                organization_aware_split=False, split_seed="seed",
                train_percent=80, val_percent=10, split_reconciliation_plan=None,
            )
            left = self.design("left", "https://forge.example/org/left.git")
            right = self.design("right", "https://forge.example/org/right.git")
            right["build"]["top_module"] = "right_top"
            right["dedup"] = {
                "source_hash": "e" * 64, "normalized_hash": "f" * 64,
                "hierarchy_hash": "1" * 64, "generic_netlist_hash": "2" * 64,
            }
            designs = {"left": left, "right": right}
            rtl.assign_families_and_splits(designs, corpus, args)
            assignments = rtl.load_jsonl(
                corpus / "manifests/split_assignments.jsonl", "split_group_id"
            )
            groups = sorted(assignments)
            assignments[groups[0]]["split"] = "train"
            assignments[groups[1]]["split"] = "test"
            rtl.write_jsonl(corpus / "manifests/split_assignments.jsonl", assignments.values())
            round_id = "p2r_test_profile_transition"
            round_dir = corpus / "quality/phase2/rounds" / round_id
            round_dir.mkdir(parents=True)
            cohort_path = round_dir / "cohort_lock.json"
            cohort_path.write_text(json.dumps({"acquired_revision_count": 2}) + "\n")
            cohort_hash = hashlib.sha256(cohort_path.read_bytes()).hexdigest()
            (round_dir / "cohort_lock.admission.json").write_text(json.dumps({
                "schema": "rtl_immutable_artifact_admission_v1",
                "object_id": "cohort_lock.json", "sha256": cohort_hash,
                "size": cohort_path.stat().st_size, "rehash_required": False,
            }) + "\n")
            audit_path = round_dir / "split_profile_consumption_audit.json"
            audit_path.write_text(json.dumps({
                "schema": "rtl_split_profile_consumption_audit_v1",
                "status": "NO_RECORDED_CONSUMPTION",
            }) + "\n")
            audit_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest()
            (round_dir / "split_profile_consumption_audit.json.admission.json").write_text(json.dumps({
                "schema": "rtl_immutable_artifact_admission_v1",
                "object_id": "split_profile_consumption_audit.json",
                "sha256": audit_hash, "size": audit_path.stat().st_size,
                "rehash_required": False,
            }) + "\n")
            plan_path = round_dir / "split_reconciliation_plan.json"
            plan_path.write_text(json.dumps(self.canonical_plan({
                "schema": rtl.SPLIT_PROFILE_TRANSITION_SCHEMA,
                "round_id": round_id,
                "split_epoch": "test_profile_v2",
                "policy": "CONSERVATIVE_SPLIT_PROMOTION_V1",
                "reason": "NEW_CROSS_TEST_CLOSURE_EVIDENCE",
                "cohort_revision_count": 2,
                "cohort_lock_sha256": cohort_hash,
                "old_profile": {
                    "profile_id": "rtl_split_profile_v1",
                    "split_schema": "rtl_split_v1", "status_after": "SUPERSEDED",
                },
                "new_profile": {
                    "profile_id": "rtl_split_profile_v2",
                    "split_schema": "rtl_split_v2", "status": "CURRENT",
                },
                "consumption_audit": {
                    "path": str(audit_path),
                    "sha256": audit_hash,
                },
                "components": [{
                    "old_split_groups": groups,
                    "old_splits": ["test", "train"],
                    "target_split": "test",
                }],
            })))
            args.split_reconciliation_plan = plan_path
            shared_hash = "8" * 64
            for record in designs.values():
                record["source"]["source_units"].append(
                    {"path": "shared.v", "language": "verilog", "sha256": shared_hash}
                )
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual({record["split"] for record in designs.values()}, {"test"})
            self.assertEqual({record["split_schema"] for record in designs.values()}, {"rtl_split_v2"})
            self.assertEqual({record["split_profile_id"] for record in designs.values()}, {"rtl_split_profile_v2"})
            profiles = rtl.load_jsonl(
                corpus / "manifests/split_profiles.jsonl", "profile_id"
            )
            self.assertEqual(profiles["rtl_split_profile_v1"]["status"], "SUPERSEDED")
            self.assertEqual(profiles["rtl_split_profile_v2"]["status"], "CURRENT")
            reconciliation = next(iter(rtl.load_jsonl(
                corpus / "manifests/split_reconciliations.jsonl", "reconciliation_id"
            ).values()))
            self.assertEqual(reconciliation["new_split"], "test")
            self.assertEqual(reconciliation["profile_transition_schema"], rtl.SPLIT_PROFILE_TRANSITION_SCHEMA)

            # A later ordinary round inherits the sole CURRENT profile instead
            # of silently reverting terminal assignments to v1.
            args.split_reconciliation_plan = None
            rtl.assign_families_and_splits(designs, corpus, args)
            self.assertEqual({record["split_profile_id"] for record in designs.values()}, {"rtl_split_profile_v2"})
            self.assertEqual({record["split_schema"] for record in designs.values()}, {"rtl_split_v2"})
            rtl.validate_publish_invariants(
                corpus, designs,
                {
                    "split_assignments": rtl.load_jsonl(
                        corpus / "manifests/split_assignments.jsonl", "split_group_id"
                    ),
                    "membership_index": rtl.load_jsonl(
                        corpus / "manifests/split_membership_index.jsonl", "member"
                    ),
                    "reconciliation_index": rtl.load_jsonl(
                        corpus / "manifests/split_reconciliations.jsonl", "reconciliation_id"
                    ),
                    "profile_index": profiles,
                },
            )

    def test_legacy_supersession_lineage_is_backfilled_from_merged_target(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            manifests = corpus / "manifests"
            manifests.mkdir(parents=True)
            rtl.write_jsonl(manifests / "split_assignments.jsonl", [
                {
                    "split_group_id": "sg_old", "split": "train",
                    "group_members": ["family:f_old"],
                    "superseded_by": "sg_merged",
                },
                {
                    "split_group_id": "sg_merged", "split": "train",
                    "group_members": ["family:f_old", "family:f_new"],
                    "merged_from": ["sg_old"],
                    "grouping_evidence": ["project_target", "source_closure"],
                },
            ])
            args = SimpleNamespace(
                organization_aware_split=False, split_seed="seed",
                train_percent=80, val_percent=10, split_reconciliation_plan=None,
            )
            rtl.assign_families_and_splits({}, corpus, args)
            assignments = rtl.load_jsonl(
                manifests / "split_assignments.jsonl", "split_group_id"
            )
            lineage = assignments["sg_old"]["supersession_lineage"]
            self.assertEqual(lineage["reason"], "LEGACY_CLOSURE_MERGE_LINEAGE_BACKFILL")
            self.assertEqual(lineage["new_split_group"], "sg_merged")
            self.assertEqual(lineage["new_split"], "train")

    def test_mixed_source_units_are_per_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            verilog = repo / "top.sv"
            vhdl = repo / "ip.vhd"
            verilog.write_text("module top(input a, output y); endmodule\n")
            vhdl.write_text("entity ip is port(a: in bit; y: out bit); end ip;\n")
            units = rtl.source_unit_records([verilog, vhdl], repo)
            self.assertEqual({unit["language"] for unit in units}, {"systemverilog", "vhdl"})
            self.assertEqual({unit["path"] for unit in units}, {"top.sv", "ip.vhd"})

    def test_pure_vhdl_frontend_produces_canonical_verilog(self):
        yosys = Path("/opt/OpenROAD/oss-cad-suite/bin/yosys")
        if not yosys.exists():
            self.skipTest("Yosys/GHDL toolchain unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "and_gate.vhd"
            source.write_text(
                "library ieee; use ieee.std_logic_1164.all;\n"
                "entity and_gate is port(a,b: in std_logic; y: out std_logic); end and_gate;\n"
                "architecture rtl of and_gate is begin y <= a and b; end rtl;\n"
            )
            result = rtl.synthesize_design("and_gate", "vhdl", ["and_gate"], [source], [root], root / "out", str(yosys), 30)
            self.assertTrue(result["generic_pass"], result)
            self.assertTrue(Path(result["generic_netlist"]).exists())

    def test_verilog_top_can_close_over_vhdl_child(self):
        yosys = Path("/opt/OpenROAD/oss-cad-suite/bin/yosys")
        if not yosys.exists():
            self.skipTest("Yosys/GHDL toolchain unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "and_gate.vhd"
            top = root / "top.sv"
            child.write_text(
                "library ieee; use ieee.std_logic_1164.all;\n"
                "entity and_gate is port(a,b: in std_logic; y: out std_logic); end and_gate;\n"
                "architecture rtl of and_gate is begin y <= a and b; end rtl;\n"
            )
            top.write_text("module top(input logic a,b, output logic y); and_gate u(.a(a),.b(b),.y(y)); endmodule\n")
            result = rtl.synthesize_design("top", "systemverilog", ["and_gate"], [child, top], [root], root / "out", str(yosys), 30)
            self.assertTrue(result["generic_pass"], result)
            self.assertEqual(result["frontend"], "mixed_language")

    def test_release_eligibility_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "LICENSE").write_text("MIT License\nPermission is hereby granted")
            result = rtl.license_evidence(repo)
            self.assertEqual(result["license_status"], "PERMISSIVE_CONFIRMED")
            self.assertEqual(result["release_policy"], "PUBLIC_EXPORT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
