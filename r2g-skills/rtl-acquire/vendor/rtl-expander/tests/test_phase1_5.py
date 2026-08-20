import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_failure_cohort
import audit_mixed_language_vhdl
import functional_ontology
import finalize_recovered_candidates
import recover_license_evidence
import run_expansion_round
import run_mapping_cohort
import scheduler


class Phase15Tests(unittest.TestCase):
    def test_function_ontology_distinguishes_complex_targets(self):
        record = {
            "identity": {"repository_name": "soc"},
            "build": {"top_module": "pcie_ddr_controller", "dependency_modules": []},
            "rtl_semantics": {}, "source": {"source_units": []},
        }
        self.assertEqual(functional_ontology.classify(record)["label"], "pcie")
        self.assertEqual(functional_ontology.ONTOLOGY_SCHEMA, "rtl_function_ontology_v2")
        classified = functional_ontology.classify(record)
        self.assertEqual(classified["diversity_weight"], functional_ontology.CONFIDENCE_WEIGHTS[classified["confidence"]])

    def test_license_conflict_remains_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("MIT License\nPermission is hereby granted, free of charge")
            (root / "core.sv").write_text("// SPDX-License-Identifier: GPL-3.0-only\nmodule core; endmodule\n")
            result = recover_license_evidence.recover(root)
            self.assertIn("LICENSE_CONFLICT", result["resolution_states"])
            self.assertEqual(result["license_status"], "UNKNOWN")
            self.assertEqual(result["release_policy"], "QUARANTINE")

    def test_registry_requires_every_named_benchmark(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "verilog_eval").mkdir()
            digest = "a" * 64
            (root / "verilog_eval" / "fingerprints.jsonl").write_text(json.dumps({"raw_hash": digest}) + "\n")
            catalog = {
                "ready": False,
                "entries": {"verilog_eval": {"benchmark": "verilog_eval", "status": "ACTIVE", "fingerprints": 1}},
            }
            (root / "registry_catalog.json").write_text(json.dumps(catalog))
            hashes, ready = run_expansion_round.load_benchmark_hashes(root)
            self.assertIn(digest, hashes)
            self.assertFalse(ready)

    def test_failure_triage_maps_missing_module_to_r1(self):
        row = {
            "failure_type": "GENERIC_SYNTH_FAIL", "top_candidate": "top",
            "detail": "Module child referenced in module top is not part of the design",
        }
        label, confidence, evidence = audit_failure_cohort.diagnose(row)
        self.assertEqual(label, "BUILD_CONTEXT_RECOVERABLE")
        self.assertEqual(confidence, "HIGH")
        self.assertTrue(evidence)

    def test_failure_adjudication_abstains_without_evidence(self):
        row = {"failure_type": "PARSE_FAIL", "top_candidate": "core", "detail": "parser failed"}
        label, confidence, evidence = audit_failure_cohort.diagnose(row)
        self.assertEqual(label, "ABSTAIN")
        self.assertEqual(confidence, "LOW")
        self.assertTrue(evidence)
        self.assertEqual(audit_failure_cohort.AUDIT_SCHEMA, "rtl_failure_adjudication_v2")

    def test_mixed_language_adjudication_detects_frontend_gap(self):
        row = {
            "failure_type": "MIXED_LANGUAGE_VHDL_TOP_UNSUPPORTED",
            "detail": "mixed_language_vhdl_top_unsupported",
            "source_units": [{"language": "vhdl"}, {"language": "systemverilog"}],
        }
        label, confidence, evidence = audit_mixed_language_vhdl.adjudicate(row)
        self.assertEqual(label, "FRONTEND_LIMITATION")
        self.assertEqual(confidence, "HIGH")
        self.assertTrue(evidence)

    def test_final_repair_structural_gate_rejects_undriven_wire(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "yosys.log"
            log.write_text("Found and reported 0 problems.\nWarning: Wire top.x is used but has no driver.\n")
            passed, failures = finalize_recovered_candidates.structural_evidence({"generic_pass": True, "log_path": str(log)})
            self.assertFalse(passed)
            self.assertIn("UNDRIVEN_SIGNAL", failures)

    def test_mapping_cohort_includes_resource_classes(self):
        records = []
        for resource in ("TINY", "SMALL", "MEDIUM", "LARGE", "XLARGE"):
            records.append({
                "family_id": f"f_{resource}", "design_id": f"d_{resource}",
                "resource": {"class": resource}, "source": {"source_languages": ["verilog"], "source_units": []},
                "identity": {"repository_name": resource}, "build": {"top_module": "misc", "dependency_modules": []},
                "rtl_semantics": {},
            })
        selected = run_mapping_cohort.select_cohort(records, 5, "seed")
        self.assertEqual({row["resource"]["class"] for row in selected}, {"TINY", "SMALL", "MEDIUM", "LARGE", "XLARGE"})

    def test_phase15_scheduler_penalizes_gitlab_generic_keyword(self):
        github = scheduler.deterministic_priority("pcie large rtl", provider="github", strategy="keyword")
        gitlab = scheduler.deterministic_priority("pcie large rtl", provider="gitlab", strategy="keyword")
        dependency = scheduler.deterministic_priority("pcie large rtl", provider="github", strategy="dependency")
        self.assertGreater(github, gitlab)
        self.assertGreater(dependency, github)

    def test_phase2_scheduler_prioritizes_mixed_language_and_scale(self):
        mixed = scheduler.deterministic_priority("mixed-language vhdl verilog soc")
        simple = scheduler.deterministic_priority("simple-peripheral fifo counter")
        self.assertGreater(mixed, simple)

    def test_phase2_scheduler_does_not_calibrate_from_provisional_round(self):
        with tempfile.TemporaryDirectory() as directory:
            db = scheduler.FrontierDB(Path(directory) / "frontier.sqlite")
            try:
                changed, state = scheduler.calibrate_phase2_round(db, {
                    "factory_round_id": "p2r_test", "yield_status": "PROVISIONAL_PENDING_PROCESSING",
                    "acquisition_cohort": {"new_acquired_revisions": 500},
                })
                self.assertEqual(changed, 0)
                self.assertEqual(state, "SKIPPED_PROVISIONAL_ROUND")
            finally:
                db.close()

    def test_gold_family_view_selects_only_gold_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common = {
                "family_id": "f_one", "family": {"family_confidence": "HIGH", "family_evidence": []},
                "dedup": {"normalized_hash": "n", "generic_netlist_hash": "g"},
                "synthesis": {"status": "SYNTH_GENERIC_ONLY"}, "split": "train",
                "release": {"release_policy": "PUBLIC_EXPORT_ALLOWED"},
            }
            designs = {
                "d_gold": common | {"design_id": "d_gold", "quality": {"training_tier": "TRAINING_GOLD"}},
                "d_silver": common | {"design_id": "d_silver", "quality": {"training_tier": "TRAINING_SILVER"}},
            }
            run_expansion_round.write_manifests(root, designs)
            view = json.loads((root / "manifests/training_gold_families.jsonl").read_text())
            self.assertEqual(view["eligible_design_ids"], ["d_gold"])
            self.assertEqual(view["variant_selection_policy"], "GOLD_ELIGIBLE_DESIGN_INSTANCES_ONLY")


if __name__ == "__main__":
    unittest.main()
