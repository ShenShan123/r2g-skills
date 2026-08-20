import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import acquire_frontier as acquisition
import discovery_providers
import discovery_evidence
import discover_repositories as discovery
import frontier
import run_expansion_round as processor
import run_factory_round as factory
import run_until_revision_target as target_controller
import scheduler
import summarize_scale_pilot as pilot_summary
import summarize_phase2
import phase2_round_delta
from processing_queue import ProcessingQueue

import materialize_storage_layout as storage_layout


class FrontierTests(unittest.TestCase):
    def test_multi_evidence_missing_metadata_is_neutral(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/unknown"}, query_text="misc", strategy="keyword"
        )
        self.assertEqual(evidence["score"], 0.4)
        self.assertFalse(evidence["negative_evidence"])

    def test_language_and_graph_evidence_survive_missing_description(self):
        language = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/core", "primary_language": "SystemVerilog"},
            query_text="language:SystemVerilog created:2025-01-01..2025-12-31",
            strategy="language_coverage",
        )
        graph = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/child"}, strategy="submodule", graph_source_trusted=True,
        )
        self.assertGreaterEqual(language["score"], 0.8)
        self.assertLess(graph["score"], 0.5)
        self.assertTrue(graph["exploration_eligible"])
        self.assertEqual(graph["admission_anchor"], "GRAPH_ONLY")
        self.assertGreaterEqual(graph["priority_bonus"], 3.0)

    def test_organization_only_evidence_cannot_enter_precision(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/known-hardware-org/ambiguous-neighbor"},
            strategy="organization_sibling", graph_source_trusted=True,
        )
        self.assertEqual(evidence["score"], 0.45)
        self.assertEqual(evidence["admission_anchor"], "ORGANIZATION_ONLY")
        self.assertTrue(evidence["exploration_eligible"])

    def test_direct_hdl_anchors_enter_precision_and_are_attributed(self):
        cases = [
            ({"url": "https://github.com/example/core", "primary_language": "VHDL"}, "misc", "DIRECT_HDL_LANGUAGE"),
            ({"url": "https://github.com/example/core", "core_path": "rtl/top.sv"}, "misc", "DIRECT_HDL_FILE"),
            ({"url": "https://github.com/example/core", "core_path": "core.core"}, "misc", "HDL_MANIFEST"),
            ({"url": "https://github.com/example/core"}, "language:Verilog created:2025-01-01..2025-12-31", "RTL_QUERY"),
        ]
        for candidate, query, anchor in cases:
            with self.subTest(anchor=anchor):
                evidence = discovery_evidence.score_discovery_evidence(
                    candidate, query_text=query, strategy="keyword",
                )
                self.assertGreaterEqual(evidence["score"], 0.5)
                self.assertEqual(evidence["admission_anchor"], anchor)

    def test_multi_anchor_admission_is_explicit(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/core", "primary_language": "Verilog", "core_path": "rtl/top.v"},
            query_text="language:Verilog created:2025-01-01..2025-12-31", strategy="language_coverage",
        )
        self.assertEqual(evidence["admission_anchor"], "MULTI_EVIDENCE")
        self.assertEqual(set(evidence["rtl_anchors"]), {"DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "RTL_QUERY"})

    def test_strong_non_rtl_without_direct_anchor_is_not_admitted(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/node-cpu", "primary_language": "JavaScript", "description": "Node.js web app"},
            query_text="systemverilog processor rtl", strategy="organization_sibling", graph_source_trusted=True,
        )
        self.assertLess(evidence["score"], 0.5)
        self.assertFalse(evidence["exploration_eligible"])
        self.assertTrue(evidence["strong_non_rtl_evidence"])

    def test_cpp_and_python_are_not_blanket_non_rtl_negatives(self):
        for language in ("C++", "Python"):
            with self.subTest(language=language):
                evidence = discovery_evidence.score_discovery_evidence(
                    {"url": "https://github.com/example/ambiguous", "primary_language": language},
                    strategy="organization",
                )
                self.assertFalse(evidence["strong_non_rtl_evidence"])
                self.assertTrue(evidence["exploration_eligible"])

    def test_explicit_software_negative_is_not_promoted(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://gitlab.com/example/cpu-driver", "description": "Linux kernel driver"},
            query_text="riscv core", strategy="keyword",
        )
        self.assertLess(evidence["score"], 0.5)
        self.assertFalse(evidence["exploration_eligible"])

    def test_language_window_recursively_splits_capped_results(self):
        query = "language:Verilog created:2024-01-01..2024-12-31"
        children = discovery_evidence.split_language_date_query(query, 1500)
        self.assertEqual(len(children), 2)
        self.assertIn("2024-01-01..2024-07-01", children[0])
        self.assertIn("2024-07-02..2024-12-31", children[1])
        self.assertEqual(discovery_evidence.split_language_date_query(query, 999), [])

    def test_domain_query_is_bounded_exploration_not_global_threshold_drop(self):
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/mystery"}, query_text="riscv core", strategy="keyword"
        )
        self.assertLess(evidence["score"], 0.5)
        self.assertTrue(evidence["exploration_eligible"])

    def test_scale_evidence_changes_priority_not_design_likelihood(self):
        candidate = {
            "url": "https://github.com/example/core",
            "primary_language": "Verilog",
        }
        ordinary = discovery_evidence.score_discovery_evidence(
            candidate, query_text="misc", strategy="keyword",
        )
        large = discovery_evidence.score_discovery_evidence(
            {**candidate, "size_bytes": 50 * 1024 * 1024},
            query_text="misc", strategy="keyword",
        )
        self.assertEqual(large["score"], ordinary["score"])
        self.assertGreater(large["priority_bonus"], ordinary["priority_bonus"])
        self.assertIn("PROVIDER_REPOSITORY_SIZE", large["scale_evidence"])

    def test_provider_scoring_uses_only_query_terms_the_api_executes(self):
        query = "noc systemverilog synthesizable"
        self.assertEqual(discovery_evidence.effective_provider_query("gitlab", query), "noc")
        self.assertEqual(discovery_evidence.effective_provider_query("codeberg", query), "noc")
        self.assertEqual(discovery_evidence.effective_provider_query("github", query), query)
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://gitlab.com/example/noc-memorial"},
            query_text=discovery_evidence.effective_provider_query("gitlab", query),
            strategy="keyword",
        )
        self.assertLess(evidence["score"], 0.5)
        self.assertEqual(evidence["admission_anchor"], "QUERY_ONLY")

    def test_precision_policy_treats_rtl_query_as_origin_not_content(self):
        policy = {
            "status": "ACTIVE",
            "admission_anchor_cells": {
                "RTL_QUERY_ORIGIN": {"tier": "EXPLORATION", "design_families_per_revision": 0.01},
            },
        }
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/mystery"},
            query_text="language:Verilog created:2025-01-01..2025-12-31",
            strategy="language_coverage", precision_policy=policy,
        )
        self.assertEqual(evidence["schema"], "rtl_discovery_evidence_v2")
        self.assertEqual(evidence["admission_anchor"], "RTL_QUERY_ORIGIN")
        self.assertEqual(evidence["rtl_content_evidence"], [])
        self.assertLess(evidence["score"], 0.5)
        self.assertEqual(evidence["admission_tier"], "EXPLORATION")

    def test_verified_semantic_graph_can_enter_precision(self):
        policy = {"status": "ACTIVE", "admission_anchor_cells": {}}
        evidence = discovery_evidence.score_discovery_evidence(
            {"url": "https://github.com/example/dependency"}, strategy="dependency",
            graph_source_trusted=True, graph_evidence_kind="VERIFIED_RTL_DEPENDENCY",
            precision_policy=policy,
        )
        self.assertEqual(evidence["admission_anchor"], "VERIFIED_RTL_GRAPH_NEIGHBOR")
        self.assertIn("VERIFIED_RTL_DEPENDENCY", evidence["rtl_content_evidence"])
        self.assertGreaterEqual(evidence["score"], 0.5)
        self.assertEqual(evidence["admission_tier"], "PRODUCTION")

    def test_precision_claim_excludes_dormant_cells(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            for name, tier in (("good", "PRODUCTION"), ("bad", "DORMANT")):
                db.upsert_repository({
                    "url": f"https://github.com/test/{name}", "design_likelihood": 0.9,
                    "discovery_evidence": {"admission_tier": tier},
                }, "keyword", priority=2.0)
            row = db.claim_repository("worker", precision_policy=True)
            self.assertEqual(row["repository_key"], "github:test/good")

    def test_large_repository_lane_requires_direct_rtl_anchor(self):
        keyword_only = {
            "design_likelihood": 0.95,
            "metadata_json": json.dumps({
                "description": "large synthesizable rtl soc",
                "discovery_evidence": {
                    "rtl_anchors": ["RTL_QUERY"],
                },
            }),
        }
        direct_hdl = {
            "design_likelihood": 0.95,
            "metadata_json": json.dumps({
                "discovery_evidence": {
                    "rtl_anchors": ["DIRECT_HDL_LANGUAGE"],
                },
            }),
        }
        self.assertFalse(acquisition.strong_rtl_confidence(keyword_only))
        self.assertTrue(acquisition.strong_rtl_confidence(direct_hdl))

    def test_persistent_worker_specs_reserve_unknown_and_slow_capacity(self):
        self.assertEqual(acquisition.persistent_worker_specs(3, 1, 1), [
            ("slow", 0), ("unknown", 0),
            ("fast", 0), ("fast", 1), ("fast", 2),
        ])
        self.assertEqual(acquisition.lane_steal_order("fast"), ("fast", "unknown", "slow"))

    def test_size_lanes_claim_only_their_estimated_repository_class(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.upsert_repository({
                "url": "https://github.com/test/fast", "design_likelihood": 0.9,
                "size_bytes": 1024,
            }, "keyword", priority=2.0)
            db.upsert_repository({
                "url": "https://github.com/test/slow", "design_likelihood": 0.9,
                "size_bytes": 128 * 1024 * 1024,
            }, "keyword", priority=2.0)
            db.upsert_repository({
                "url": "https://github.com/test/unknown", "design_likelihood": 0.9,
            }, "keyword", priority=2.0)
            fast = db.claim_repository("fast-worker", size_lane="fast", size_threshold_bytes=64 * 1024 * 1024)
            unknown = db.claim_repository("unknown-worker", size_lane="unknown", size_threshold_bytes=64 * 1024 * 1024)
            slow = db.claim_repository("slow-worker", size_lane="slow", size_threshold_bytes=64 * 1024 * 1024)
            self.assertEqual(fast["repository_key"], "github:test/fast")
            self.assertEqual(unknown["repository_key"], "github:test/unknown")
            self.assertEqual(slow["repository_key"], "github:test/slow")

    def test_round_global_exploration_cap_is_atomic_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frontier.sqlite"
            with frontier.FrontierDB(path) as db:
                db.initialize_round_acquisition_budget(
                    "round-a", 10, 0.20, "2026-01-01T00:00:00+00:00"
                )
                for index in range(4):
                    db.upsert_repository({
                        "url": f"https://github.com/test/explore-{index}",
                        "design_likelihood": 0.4,
                        "discovery_evidence": {
                            "admission_tier": "EXPLORATION",
                            "exploration_eligible": True,
                        },
                    }, "graph", priority=2.0)
                first = db.claim_repository(
                    "worker-a", exploration=True, precision_policy=True,
                    round_id="round-a",
                )
                second = db.claim_repository(
                    "worker-b", exploration=True, precision_policy=True,
                    round_id="round-a",
                )
                denied = db.claim_repository(
                    "worker-c", exploration=True, precision_policy=True,
                    round_id="round-a",
                )
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                self.assertIsNone(denied)
                status = db.round_acquisition_budget_status("round-a")
                self.assertEqual(status["exploration_cap"], 2)
                self.assertEqual(status["exploration_active_claims"], 2)
            with frontier.FrontierDB(path) as reopened:
                status = reopened.initialize_round_acquisition_budget(
                    "round-a", 10, 0.20, "2026-01-01T00:00:00+00:00"
                )
                self.assertEqual(status["exploration_remaining_claim_capacity"], 0)

    def test_graph_only_exploration_has_round_global_low_value_subcap(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(
            Path(directory) / "frontier.sqlite"
        ) as db:
            db.initialize_round_acquisition_budget(
                "round-low-value", 100, 0.20, "2026-01-01T00:00:00+00:00"
            )
            for index in range(6):
                db.upsert_repository({
                    "url": f"https://github.com/test/graph-{index}",
                    "design_likelihood": 0.4,
                    "discovery_evidence": {
                        "admission_tier": "EXPLORATION",
                        "admission_anchor": "GRAPH_ONLY",
                        "exploration_eligible": True,
                    },
                }, "graph", priority=3.0)
            for index in range(2):
                db.upsert_repository({
                    "url": f"https://github.com/test/organization-{index}",
                    "design_likelihood": 0.4,
                    "discovery_evidence": {
                        "admission_tier": "EXPLORATION",
                        "admission_anchor": "ORGANIZATION_ONLY",
                        "exploration_eligible": True,
                    },
                }, "organization", priority=2.0)

            first = db.claim_repository(
                "worker-first", exploration=True, precision_policy=True,
                round_id="round-low-value",
            )
            second = db.claim_repository(
                "worker-second", exploration=True, precision_policy=True,
                round_id="round-low-value",
            )
            self.assertIn("organization-", first["repository_key"])
            self.assertIn("organization-", second["repository_key"])

            graph_claims = [
                db.claim_repository(
                    f"worker-graph-{index}", exploration=True, precision_policy=True,
                    round_id="round-low-value",
                )
                for index in range(5)
            ]
            self.assertEqual(sum(row is not None for row in graph_claims), 4)
            status = db.round_acquisition_budget_status("round-low-value")
            self.assertEqual(status["low_value_exploration_cap"], 4)
            self.assertEqual(status["low_value_exploration_used"], 4)

    def test_executor_attempt_budget_is_shared_across_workers(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(
            Path(directory) / "frontier.sqlite"
        ) as db:
            db.initialize_round_acquisition_budget(
                "round-b", 20, 0.15, "2026-01-01T00:00:00+00:00"
            )
            db.initialize_acquisition_executor("exec-b", "round-b", 3)
            for index in range(6):
                db.upsert_repository({
                    "url": f"https://github.com/test/production-{index}",
                    "design_likelihood": 0.9,
                    "size_bytes": 1024,
                    "discovery_evidence": {"admission_tier": "PRODUCTION"},
                }, "language", priority=2.0)
            claimed = [
                db.claim_repository(
                    f"worker-{index % 2}", precision_policy=True,
                    round_id="round-b", executor_id="exec-b", size_lane="fast",
                )
                for index in range(4)
            ]
            self.assertEqual(sum(row is not None for row in claimed), 3)
            executor = db.connection.execute(
                "SELECT attempts_claimed FROM acquisition_executor_budget WHERE executor_id='exec-b'"
            ).fetchone()
            self.assertEqual(executor[0], 3)

    def test_batch_five_is_sequential_baseline_and_batch_six_is_parallel(self):
        def arguments(round_id: str, mode: str = "auto") -> SimpleNamespace:
            return SimpleNamespace(
                acquisition_execution_mode=mode,
                factory_round_id=round_id,
                parallel_acquisition_activation_batch=6,
                parallel_acquisition_fast_workers=4,
                parallel_acquisition_unknown_workers=1,
                parallel_acquisition_slow_workers=1,
                parallel_acquisition_slow_fraction=0.2,
                parallel_acquisition_size_threshold_bytes=64 * 1024 * 1024,
            )

        self.assertEqual(
            target_controller.acquisition_execution_mode(arguments("family_20260812_batch0005")),
            "sequential",
        )
        self.assertEqual(
            target_controller.acquisition_execution_mode(arguments("family_20260812_batch0006")),
            "bounded_parallel",
        )
        self.assertEqual(
            target_controller.acquisition_execution_mode(arguments("family_20260812_batch0006", "sequential")),
            "sequential",
        )

    def test_auto_reconciliation_accepts_only_exact_train_val_conflict(self):
        stage = {
            "stderr_tail": (
                "RuntimeError: frozen split conflict: closure/hierarchy/project component "
                "would merge groups ['sg_a', 'sg_b'] across splits ['train', 'val']"
            )
        }
        self.assertEqual(
            target_controller.deterministic_train_val_conflict(stage), ["sg_a", "sg_b"]
        )
        stage["stderr_tail"] = stage["stderr_tail"].replace("'val'", "'test'")
        self.assertIsNone(target_controller.deterministic_train_val_conflict(stage))

    def test_auto_reconciliation_reads_nested_factory_failure_from_log(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout_log = Path(directory) / "factory.stdout.log"
            stdout_log.write_text(json.dumps({
                "stages": [{
                    "stage": "processing_acquired",
                    "stderr_tail": (
                        "RuntimeError: frozen split conflict: closure/hierarchy/project component "
                        "would merge groups ['sg_old', 'sg_new'] across splits ['train', 'val']"
                    ),
                }],
            }), encoding="utf-8")
            stage = {"stderr_tail": "", "stdout_tail": "", "stdout_log": str(stdout_log)}
            self.assertEqual(
                target_controller.deterministic_train_val_conflict(stage),
                ["sg_new", "sg_old"],
            )

    def test_test_boundary_conflict_is_parsed_but_not_train_val(self):
        stage = {"stderr_tail": (
            "RuntimeError: frozen split conflict involving test: "
            "closure/hierarchy/project component would merge groups "
            "['sg_train', 'sg_test'] across splits ['test', 'train']; "
            "requires a new benchmark/split profile or quarantine"
        )}
        self.assertEqual(target_controller.deterministic_split_conflict(stage), {
            "old_split_groups": ["sg_test", "sg_train"],
            "old_splits": ["test", "train"],
        })
        self.assertIsNone(target_controller.deterministic_train_val_conflict(stage))

    def test_test_profile_rollover_requires_separately_authored_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "lacks a campaign consumption contract"):
                target_controller.campaign_consumption_contract(
                    corpus, "p2f_20260812_design-family-10k_batch0007"
                )

    def test_batch_sequence_controls_versioned_recovery_activation(self):
        self.assertEqual(target_controller.batch_sequence("objective_batch0005"), 5)
        self.assertEqual(target_controller.batch_sequence("objective_batch0006"), 6)
        self.assertEqual(target_controller.batch_sequence("unversioned"), 0)

    def test_repository_urls_deduplicate_before_acquisition(self):
        urls = [
            "https://github.com/Foo/Bar", "https://github.com/foo/bar.git",
            "git@github.com:foo/bar.git", "github.com/foo/bar/",
            "https://github.com/foo/bar/blob/main/rtl/top.v",
        ]
        keys = {frontier.canonical_repository_identity(url)["repository_key"] for url in urls}
        self.assertEqual(keys, {"github:foo/bar"})

    def test_multiple_discovery_paths_create_one_repository(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            _, first = db.upsert_repository({"url": "https://github.com/foo/bar"}, "keyword")
            _, second = db.upsert_repository({"url": "git@github.com:foo/bar.git"}, "dependency")
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(db.counts()["repositories"], 1)
            self.assertEqual(db.counts()["discovery_events"], 2)

    def test_query_cursor_and_provider_backoff_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frontier.sqlite"
            with frontier.FrontierDB(path) as db:
                query = db.add_query("github", "keyword", "axi rtl", 1, 10)
                db.update_query(query, cursor="7", state="RETRY", next_run_at="2099-01-01T00:00:00+00:00")
                db.update_provider_state("github", backoff_until="2099-01-01T00:00:00+00:00")
            with frontier.FrontierDB(path) as db:
                row = db.connection.execute("SELECT * FROM queries WHERE query_id=?", (query,)).fetchone()
                provider = db.connection.execute("SELECT * FROM provider_state WHERE provider='github'").fetchone()
                self.assertEqual(row["cursor"], "7")
                self.assertEqual(row["state"], "RETRY")
                self.assertEqual(provider["backoff_until"], "2099-01-01T00:00:00+00:00")

    def test_atomic_claim_prevents_duplicate_workers(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.upsert_repository({"url": "https://github.com/foo/bar", "design_likelihood": 0.9}, "keyword", priority=2.0)
            first = db.claim_repository("worker-a")
            second = db.claim_repository("worker-b")
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_stale_claim_is_crash_resumable(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            key, _ = db.upsert_repository({"url": "https://github.com/foo/bar", "design_likelihood": 0.9}, "keyword", priority=2.0)
            db.claim_repository("dead-worker")
            db.connection.execute("UPDATE repositories SET claim_started_at='2000-01-01T00:00:00+00:00' WHERE repository_key=?", (key,))
            db.connection.commit()
            self.assertEqual(db.requeue_stale_claims(1), 1)
            self.assertIsNotNone(db.claim_repository("replacement-worker"))

    def test_graph_edge_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            source, _ = db.upsert_repository({"url": "https://github.com/org/source"}, "keyword")
            db.add_edge(source, "https://github.com/org/dependency", "submodule")
            db.add_edge(source, "git@github.com:org/dependency.git", "submodule")
            self.assertEqual(db.counts()["repo_edges"], 1)
            self.assertEqual(db.counts()["repositories"], 2)

    def test_file_page_candidate_is_quarantined_and_self_edge_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            source, _ = db.upsert_repository({"url": "https://github.com/org/source"}, "keyword")
            target = db.add_edge(source, "https://github.com/org/source/blob/main/rtl/top.v", "readme_reference")
            self.assertEqual(target, source)
            self.assertEqual(db.counts()["repo_edges"], 0)
            with self.assertRaises(ValueError):
                frontier.canonical_repository_identity("https://github.com/user-attachments/assets/deadbeef")
            with self.assertRaises(ValueError):
                frontier.canonical_repository_identity("https://github.com/ultraembedded/core_usb_phy](https:")

    def test_scheduler_is_deterministic_and_gap_aware(self):
        noc = scheduler.deterministic_priority("noc systemverilog rtl")
        uart = scheduler.deterministic_priority("uart verilog")
        self.assertGreater(noc, uart)
        self.assertEqual(noc, scheduler.deterministic_priority("noc systemverilog rtl"))
        self.assertEqual(scheduler.SCHEDULER_SCHEMA, "rtl_discovery_scheduler_v4")

    def test_batch_c_size_bonus_and_penalties(self):
        large = scheduler.deterministic_priority("synthesizable soc systemverilog rtl")
        small = scheduler.deterministic_priority("uart verilog")
        noisy = scheduler.deterministic_priority(
            "synthesizable soc systemverilog rtl", no_rtl_rate=0.8, duplicate_rate=0.5
        )
        self.assertGreater(large, small)
        self.assertGreater(large, noisy)
        self.assertEqual(scheduler.SIZE_WEIGHTS["XLARGE"], 3.0)

    def test_cpu_software_is_not_promoted_as_hardware(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            software, _ = db.upsert_repository(
                {"url": "https://gitlab.com/example/cpu-monitor", "description": "Linux CPU monitor"},
                "keyword",
            )
            rtl, _ = db.upsert_repository(
                {"url": "https://github.com/example/cpu-rtl", "description": "SystemVerilog RTL CPU core", "primary_language": "SystemVerilog"},
                "keyword",
            )
            db.reprioritize_hardware_likelihood()
            software_row = db.connection.execute("SELECT design_likelihood FROM repositories WHERE repository_key=?", (software,)).fetchone()
            rtl_row = db.connection.execute("SELECT design_likelihood FROM repositories WHERE repository_key=?", (rtl,)).fetchone()
            self.assertLess(software_row[0], 0.5)
            self.assertGreater(rtl_row[0], 0.5)

    def test_bounded_acquisition_retry_policy(self):
        self.assertTrue(acquisition.retry_allowed("REVISION_RESOLUTION", 1, False))
        self.assertFalse(acquisition.retry_allowed("REVISION_RESOLUTION", 2, False))
        self.assertFalse(acquisition.retry_allowed("ARCHIVE_TOO_LARGE", 1, False))
        self.assertTrue(acquisition.retry_allowed("ARCHIVE_TOO_LARGE", 1, True))
        self.assertFalse(acquisition.retry_allowed("ARCHIVE_TOO_LARGE", 2, True))
        self.assertTrue(acquisition.retry_allowed("PROVIDER_RATE_LIMIT", 10_000, False))

    def test_terminal_acquisition_suppression_is_not_claimable(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            key, _ = db.upsert_repository(
                {"url": "https://github.com/example/broken", "design_likelihood": 0.9}, "keyword", priority=3.0
            )
            row = db.claim_repository("worker")
            attempt = db.start_attempt(key, "archive")
            db.finish_acquisition(
                attempt, key, "FAILED", error_class="RuntimeError",
                error_detail="REVISION_RESOLUTION_FAILED:git_exit_128", retry=False,
            )
            self.assertIsNotNone(row)
            self.assertIsNone(db.claim_repository("other"))
            status = db.connection.execute("SELECT acquisition_status FROM repositories WHERE repository_key=?", (key,)).fetchone()[0]
            self.assertEqual(status, "EXCLUDED")

    def test_provider_cooldown_skips_only_rate_limited_provider(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            github, _ = db.upsert_repository(
                {"url": "https://github.com/example/rtl", "design_likelihood": 0.9},
                "keyword", priority=3.0,
            )
            gitlab, _ = db.upsert_repository(
                {"url": "https://gitlab.com/example/rtl", "design_likelihood": 0.9},
                "keyword", priority=2.0,
            )
            cooldown = db.set_provider_rate_limit("github", retry_after=3600, source="test")
            claimed = db.claim_repository("worker", providers=["github", "gitlab"])
            self.assertEqual(claimed["repository_key"], gitlab)
            self.assertNotEqual(claimed["repository_key"], github)
            self.assertEqual(cooldown["status"], "RATE_LIMITED")

    def test_discovery_stops_issuing_requests_after_mid_round_rate_limit(self):
        class RateLimitedProvider:
            name = "fusesoc"

            def __init__(self):
                self.calls = 0

            def search(self, _query, _cursor, _limit):
                self.calls += 1
                raise discovery_providers.ProviderError(
                    "HTTP 403 quota", retry_after=3600, rate_limited=True,
                )

        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.add_query("fusesoc", "keyword", "axi rtl", 2.0, 10)
            db.add_query("fusesoc", "keyword", "soc rtl", 1.0, 10)
            provider = RateLimitedProvider()
            metrics = discovery.run_query_round(db, {"fusesoc": provider}, 10, 10)
            github = db.provider_statuses(["github"])["github"]
            self.assertEqual(provider.calls, 1)
            self.assertEqual(metrics["provider_cooldown_skips"], 1)
            self.assertEqual(github["status"], "RATE_LIMITED")

    def test_rate_limit_attempt_does_not_consume_candidate_failure_budget(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            key, _ = db.upsert_repository(
                {"url": "https://github.com/example/rtl", "design_likelihood": 0.9},
                "keyword", priority=3.0,
            )
            db.claim_repository("worker")
            attempt = db.start_attempt(key, "archive")
            cooldown = db.set_provider_rate_limit("github", retry_after=600, source="test")
            db.finish_acquisition(
                attempt, key, "RATE_LIMITED", error_class="PROVIDER_RATE_LIMIT",
                error_detail="PROVIDER_RATE_LIMIT:github:HTTP_429", retry=True,
                retry_at=cooldown["reset_at"],
            )
            row = db.connection.execute(
                "SELECT acquisition_status,next_retry_at FROM repositories WHERE repository_key=?", (key,)
            ).fetchone()
            failed = db.connection.execute(
                "SELECT COUNT(*) FROM acquisition_attempts WHERE repository_key=? AND state='FAILED'", (key,)
            ).fetchone()[0]
            self.assertEqual(row["acquisition_status"], "RETRY")
            self.assertEqual(row["next_retry_at"], cooldown["reset_at"])
            self.assertEqual(failed, 0)

    def test_starvation_limit_is_watchdog_not_completion(self):
        cycles = []
        for index in range(5):
            cycles.append({"cycle": index + 1, "acquisition": {"attempts": 25, "unique_revision_successes": 0}})
        args = SimpleNamespace(
            starvation_cooldown_cycles=3, starvation_window_cycles=5,
            starvation_min_attempts=100, starvation_yield_threshold=0.05,
            max_no_progress_cycles=5, max_cycles=500,
        )
        event = target_controller.starvation_watchdog(
            {"cycles": cycles, "consecutive_no_progress_cycles": 5, "starvation_events": []}, args
        )
        self.assertIn("SLIDING_WINDOW_LOW_YIELD", event["reasons"])
        self.assertIn("CONSECUTIVE_NO_PROGRESS", event["reasons"])

    def test_provider_rate_limit_is_not_frontier_starvation(self):
        args = SimpleNamespace(
            starvation_cooldown_cycles=1, starvation_window_cycles=1,
            starvation_min_attempts=0, starvation_yield_threshold=1.0,
            max_no_progress_cycles=1, max_cycles=500,
        )
        event = target_controller.starvation_watchdog({
            "cycles": [{
                "cycle": 1, "no_progress_category": "PROVIDER_RATE_LIMIT",
                "acquisition": {"attempts": 0, "unique_revision_successes": 0},
            }],
            "consecutive_no_progress_cycles": 0, "starvation_events": [],
        }, args)
        self.assertIsNone(event)

    def test_provider_scoped_frontier_empty_is_not_frontier_starvation(self):
        args = SimpleNamespace(
            starvation_cooldown_cycles=1, starvation_window_cycles=1,
            starvation_min_attempts=0, starvation_yield_threshold=1.0,
            max_no_progress_cycles=1, max_cycles=500,
        )
        event = target_controller.starvation_watchdog({
            "cycles": [{
                "cycle": 1, "no_progress_category": "PROVIDER_SCOPED_FRONTIER_EXHAUSTED",
                "acquisition": {"attempts": 0, "unique_revision_successes": 0},
            }],
            "consecutive_no_progress_cycles": 1, "starvation_events": [],
        }, args)
        self.assertIsNone(event)

    def test_large_eligible_frontier_uses_acquisition_only_between_refresh_cadence(self):
        inventory = {
            "raw_frontier": 36_505, "acquisition_eligible_frontier": 12_000,
            "healthy_discovery_providers": ["gitlab", "codeberg"],
        }
        skipped = target_controller.discovery_decision(
            inventory, 37, frontier_threshold=10_000, cadence_cycles=15,
        )
        refreshed = target_controller.discovery_decision(
            inventory, 45, frontier_threshold=10_000, cadence_cycles=15,
        )
        self.assertFalse(skipped["run"])
        self.assertEqual(skipped["reason"], "ELIGIBLE_FRONTIER_SUFFICIENT_ACQUISITION_FIRST")
        self.assertTrue(refreshed["run"])

    def test_raw_frontier_does_not_hide_provider_scoped_empty_frontier(self):
        decision = target_controller.discovery_decision({
            "raw_frontier": 37_444, "acquisition_eligible_frontier": 0,
            "healthy_discovery_providers": ["gitlab", "codeberg"],
        }, 43, frontier_threshold=10_000, cadence_cycles=15)
        self.assertTrue(decision["run"])
        self.assertTrue(decision["targeted"])
        self.assertEqual(decision["providers"], ["gitlab", "codeberg"])
        self.assertEqual(decision["reason"], "PROVIDER_SCOPED_FRONTIER_EXHAUSTED")

    def test_frontier_empty_categories_are_mutually_exclusive(self):
        self.assertEqual(target_controller.frontier_no_progress_category({
            "all_providers_in_cooldown": True, "raw_frontier": 100,
            "acquisition_eligible_frontier": 0,
        }), "PROVIDER_RATE_LIMIT")
        self.assertEqual(target_controller.frontier_no_progress_category({
            "all_providers_in_cooldown": False, "raw_frontier": 0,
            "acquisition_eligible_frontier": 0,
        }), "FRONTIER_EXHAUSTED")
        self.assertEqual(target_controller.frontier_no_progress_category({
            "all_providers_in_cooldown": False, "raw_frontier": 100,
            "acquisition_eligible_frontier": 0,
        }), "PROVIDER_SCOPED_FRONTIER_EXHAUSTED")

    def test_quota_reserved_blocks_discovery_but_not_acquisition(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.update_provider_state(
                "github", rate_limit_remaining=8,
                rate_limit_reset="2099-01-01T00:00:00+00:00",
            )
            statuses = db.provider_statuses(["github", "fusesoc"], quota_reserve=100)
            self.assertEqual(statuses["github"]["status"], "QUOTA_RESERVED")
            self.assertEqual(statuses["fusesoc"]["status"], "QUOTA_RESERVED")
            self.assertEqual(db.discovery_eligible_providers(["github", "fusesoc"], 100), [])
            self.assertEqual(db.acquisition_eligible_providers(["github", "fusesoc"]), ["github", "fusesoc"])

    def test_successful_acquisition_canary_clears_stale_shared_zero_quota(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.update_provider_state(
                "github", rate_limit_remaining=0,
                rate_limit_reset="2000-01-01T00:00:00+00:00",
                state_json=json.dumps({"status": "RATE_LIMITED"}),
            )
            self.assertEqual(db.provider_statuses(["github"])["github"]["status"], "CANARY_READY")
            db.record_provider_success("fusesoc", source="acquisition_canary")
            statuses = db.provider_statuses(["github", "fusesoc"], quota_reserve=100)
            self.assertEqual(statuses["github"]["status"], "HEALTHY")
            self.assertEqual(statuses["fusesoc"]["status"], "HEALTHY")
            self.assertIsNone(statuses["github"]["rate_limit_remaining"])
            self.assertEqual(db.discovery_eligible_providers(["github", "fusesoc"], 100), ["github", "fusesoc"])

    def test_v431_migrates_expired_successful_canary_evidence_to_healthy(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.update_provider_state(
                "github", rate_limit_remaining=0,
                rate_limit_reset="2000-01-01T00:00:00+00:00",
                state_json=json.dumps({
                    "schema": "rtl_provider_cooldown_v1",
                    "status": "HEALTHY",
                    "provider": "github",
                    "source": "acquisition_canary",
                }),
            )
            statuses = db.provider_statuses(["github", "fusesoc"], quota_reserve=100)
            self.assertEqual(statuses["github"]["status"], "HEALTHY")
            self.assertEqual(statuses["fusesoc"]["status"], "HEALTHY")
            self.assertIsNone(statuses["github"]["rate_limit_reset"])
            row = db.connection.execute(
                "SELECT rate_limit_remaining,rate_limit_reset,state_json FROM provider_state WHERE provider='github'"
            ).fetchone()
            self.assertIsNone(row["rate_limit_remaining"])
            self.assertIsNone(row["rate_limit_reset"])
            self.assertTrue(json.loads(row["state_json"])["recovered_from_expired_zero_quota"])

    def test_v431_does_not_promote_expired_rate_limit_without_success_evidence(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.update_provider_state(
                "github", rate_limit_remaining=0,
                rate_limit_reset="2000-01-01T00:00:00+00:00",
                state_json=json.dumps({
                    "status": "RATE_LIMITED", "source": "discovery_query",
                }),
            )
            self.assertEqual(db.provider_statuses(["github"])["github"]["status"], "CANARY_READY")

    def test_v431_does_not_promote_success_evidence_before_reset(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            db.update_provider_state(
                "github", rate_limit_remaining=0,
                rate_limit_reset="2099-01-01T00:00:00+00:00",
                state_json=json.dumps({
                    "status": "HEALTHY", "source": "acquisition_canary",
                }),
            )
            self.assertEqual(db.provider_statuses(["github"])["github"]["status"], "RATE_LIMITED")

    def test_v43_eligible_watermarks_replace_legacy_ten_thousand_threshold(self):
        inventory = {
            "raw_frontier": 37_444, "acquisition_eligible_frontier": 140,
            "healthy_discovery_providers": ["gitlab", "codeberg"],
        }
        refill = target_controller.discovery_decision(
            inventory, 44, low_watermark=250, high_watermark=1000,
            cadence_cycles=15,
        )
        acquisition_first = target_controller.discovery_decision(
            {**inventory, "acquisition_eligible_frontier": 400}, 44,
            low_watermark=250, high_watermark=1000, cadence_cycles=15,
        )
        self.assertTrue(refill["run"])
        self.assertEqual(refill["reason"], "ELIGIBLE_FRONTIER_BELOW_THRESHOLD")
        self.assertEqual(refill["eligible_low_watermark"], 250)
        self.assertEqual(refill["eligible_high_watermark"], 1000)
        self.assertFalse(acquisition_first["run"])

    def test_provider_scoped_backoff_uses_earliest_reset(self):
        import datetime as dt
        reset = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)).isoformat()
        backoff = target_controller.provider_scoped_backoff({"earliest_reset_at": reset}, 300)
        self.assertEqual(backoff["wake_reason"], "PROVIDER_RESET")
        self.assertLessEqual(backoff["seconds"], 31)

    def test_early_close_requires_five_targeted_production_empty_cycles(self):
        cycle = {
            "new_acquired_revisions": 0,
            "no_progress_category": "PROVIDER_SCOPED_FRONTIER_EXHAUSTED",
            "discovery_decision": {"run": True, "targeted": True},
            "provider_status_after": {"acquisition_eligible_frontier": 0},
        }
        state = {"cycles": [{**cycle, "cycle": index} for index in range(1, 6)]}
        inventory = {
            "acquisition_eligible_frontier": 0,
            "providers": {
                "github": {"provider": "github", "quota_provider": "github", "status": "HEALTHY"},
            },
        }
        evidence = target_controller.early_close_eligibility(
            state, inventory, acquired=3000, requested_target=4000,
            exploration_remaining=0, active_claims=0,
            minimum_count=1000, minimum_fraction=0.75, required_cycles=5,
        )
        self.assertTrue(evidence["eligible"])
        self.assertEqual(evidence["reason"], "ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED")
        self.assertEqual(evidence["observed_no_yield_cycles"], 5)
        rate_limited = {**inventory, "providers": {
            "github": {"provider": "github", "quota_provider": "github", "status": "RATE_LIMITED"},
        }}
        denied = target_controller.early_close_eligibility(
            state, rate_limited, acquired=3000, requested_target=4000,
            exploration_remaining=0, active_claims=0,
            minimum_count=1000, minimum_fraction=0.75, required_cycles=5,
        )
        self.assertFalse(denied["eligible"])
        self.assertFalse(denied["checks"]["providers_not_rate_limited"])

    def test_funnel_conservation_exposes_residual(self):
        valid = pilot_summary.conservation(10, success=4, duplicate=2, quarantine=1, failure=1, skipped=2)
        invalid = pilot_summary.conservation(10, success=4, duplicate=2)
        self.assertTrue(valid["conserved"])
        self.assertEqual(valid["residual"], 0)
        self.assertFalse(invalid["conserved"])
        self.assertEqual(invalid["residual"], 4)

    def test_fusesoc_provider_deduplicates_core_files_to_repository(self):
        class FakeClient:
            def get_json(self, _url, _headers=None):
                repository = {"id": 1, "html_url": "https://github.com/example/ip", "default_branch": "main"}
                return {"total_count": 2, "items": [{"path": "a.core", "repository": repository}, {"path": "sub/b.core", "repository": repository}]}, {"x-ratelimit-remaining": "9"}
        page = discovery_providers.FuseSoCProvider(FakeClient()).search("axi", None, 10)
        self.assertEqual(len(page.repositories), 1)
        self.assertEqual(page.repositories[0]["ecosystem"], "fusesoc")

    def test_phase2_cohort_lock_freezes_exact_revision_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            start = {"factory_round_id": "p2r_test", "revision_keys": ["github:old/repo@" + "0" * 40]}
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                for index in range(3):
                    key = f"github:test/repo{index}"
                    db.upsert_repository({"url": f"https://github.com/test/repo{index}"}, "keyword")
                    revision = f"{index + 1:040x}"
                    db.connection.execute(
                        "INSERT INTO repository_revisions(repository_revision_key,repository_key,commit_sha,source_path,acquired_at) VALUES(?,?,?,?,?)",
                        (f"{key}@{revision}", key, revision, str(corpus / str(index)), frontier.utc_now()),
                    )
                db.connection.commit()
            locked = target_controller.lock_acquisition_cohort(corpus, start, 2)
            self.assertEqual(locked["acquired_revision_count"], 3)
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                key = "github:test/later"
                db.upsert_repository({"url": "https://github.com/test/later"}, "keyword")
                revision = "f" * 40
                db.connection.execute(
                    "INSERT INTO repository_revisions(repository_revision_key,repository_key,commit_sha,source_path,acquired_at) VALUES(?,?,?,?,?)",
                    (f"{key}@{revision}", key, revision, str(corpus / "later"), frontier.utc_now()),
                )
                db.connection.commit()
            selected, evidence = phase2_round_delta.select_cohort_revisions(
                corpus, start, target_controller.revision_keys(corpus)
            )
            self.assertEqual(selected, set(locked["revision_keys"]))
            self.assertNotIn(f"{key}@{revision}", selected)
            self.assertEqual(evidence["revision_keys_sha256"], locked["revision_keys_sha256"])

    def test_phase2_cohort_lock_preserves_requested_target_on_early_close(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            start = {"factory_round_id": "p2r_early", "revision_keys": []}
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                for index in range(3):
                    key = f"github:test/early{index}"
                    db.upsert_repository({"url": f"https://github.com/test/early{index}"}, "language")
                    revision = f"{index + 1:040x}"
                    db.connection.execute(
                        "INSERT INTO repository_revisions(repository_revision_key,repository_key,commit_sha,source_path,acquired_at) VALUES(?,?,?,?,?)",
                        (f"{key}@{revision}", key, revision, str(corpus / str(index)), frontier.utc_now()),
                    )
                db.connection.commit()
            evidence = {
                "schema": "rtl_eligible_frontier_early_close_evidence_v1",
                "eligible": True,
                "reason": "ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED",
                "actual_acquired_revisions": 3,
                "checks": {"all_required_conditions": True},
            }
            lock = target_controller.lock_acquisition_cohort(
                corpus, start, 4, early_close_evidence=evidence,
            )
            self.assertEqual(lock["requested_revision_target"], 4)
            self.assertEqual(lock["actual_cohort_size"], 3)
            self.assertTrue(lock["early_close"])
            target_controller.validate_cohort_lock(lock, start, 4)

    def test_factory_cache_requires_final_delta(self):
        provisional = {"state": "PASS", "stages": [{"stage": "phase2_round_delta", "state": "PASS", "yield_status": "PROVISIONAL_PENDING_PROCESSING"}]}
        final = {"state": "PASS", "completion_invariants": {"valid": True}, "stages": [{"stage": "phase2_round_delta", "state": "PASS", "yield_status": "FINAL"}]}
        self.assertFalse(factory.completion_is_final(provisional))
        self.assertFalse(target_controller.completion_is_final(provisional))
        self.assertTrue(factory.completion_is_final(final))
        self.assertTrue(target_controller.completion_is_final(final))

    def test_phase2_milestone_does_not_override_failed_current_round(self):
        hard = {
            "revision_target_met": True,
            "design_family_target_met": True,
            "duplicate_repository_revisions": 0,
            "publish_invariants_valid": True,
        }
        failed = summarize_phase2.phase_completion_ready(
            hard,
            {"state": "FAILED_FINALIZATION"},
            {"state": "FAIL", "completion_invariants": {"valid": False}},
            {"factory_round_id": "p2r_current", "yield_status": "PROVISIONAL_PENDING_PROCESSING"},
            "p2r_current",
        )
        complete = summarize_phase2.phase_completion_ready(
            hard,
            {"state": "COMPLETE"},
            {"state": "PASS", "completion_invariants": {"valid": True}},
            {"factory_round_id": "p2r_current", "yield_status": "FINAL"},
            "p2r_current",
        )
        self.assertFalse(failed)
        self.assertTrue(complete)

    def test_cohort_lock_refuses_active_acquisition_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            start = {"factory_round_id": "p2r_claim", "revision_keys": []}
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                key, _ = db.upsert_repository(
                    {"url": "https://github.com/test/claimed", "design_likelihood": 0.9},
                    "keyword", priority=2.0,
                )
                db.claim_repository("worker-a")
                revision = "1" * 40
                db.connection.execute(
                    "INSERT INTO repository_revisions(repository_revision_key,repository_key,commit_sha,source_path,acquired_at) VALUES(?,?,?,?,?)",
                    (f"{key}@{revision}", key, revision, str(corpus / "source"), frontier.utc_now()),
                )
                db.connection.commit()
            with self.assertRaisesRegex(RuntimeError, "active acquisition claims"):
                target_controller.lock_acquisition_cohort(corpus, start, 1)

    def test_terminal_revision_whitelist_excludes_running_states(self):
        commit = "1" * 40
        base = {"repository_url": "https://github.com/test/repo", "commit_sha": commit}
        self.assertEqual(
            phase2_round_delta.terminal_revision_keys([{**base, "state": "SYNTH_VALID"}]),
            {f"github:test/repo@{commit}"},
        )
        for state in ("RUNNING", "CLAIMED", "RETRY_PENDING", "STAGED", "UNKNOWN"):
            self.assertEqual(
                phase2_round_delta.terminal_revision_keys([{**base, "state": state}]), set()
            )

    def test_locked_processing_queue_is_terminal_authority_for_incremental_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            round_id = "p2f_test_batch0008"
            key = "github:example/core@" + "a" * 40
            run_key = "run-terminal"
            artifact = corpus / "state/repo_runs" / f"{run_key}.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": run_key,
                "repository": {
                    "repo_id": "repo-terminal",
                    "repository_revision_key": key,
                    "state": "NO_DESIGN",
                    "stage_status": {},
                },
                "designs": [],
            }))
            with ProcessingQueue(corpus) as queue:
                queue.enqueue(round_id, key, "/immutable/source")
                self.assertIsNotNone(queue.claim(round_id, "worker"))
                queue.finish(
                    round_id, key, terminal_state="NO_DESIGN",
                    run_key=run_key, artifact_path=str(artifact),
                )
            terminal, repositories = phase2_round_delta.locked_processing_context(
                corpus, round_id, {key},
            )
            self.assertEqual(terminal, {key})
            self.assertEqual(repositories[0]["repository_revision_key"], key)

    def test_incomplete_locked_processing_queue_remains_provisional(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            round_id = "p2f_test_batch0008"
            terminal_key = "github:example/one@" + "b" * 40
            pending_key = "github:example/two@" + "c" * 40
            run_key = "run-one"
            artifact = corpus / "state/repo_runs" / f"{run_key}.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({
                "run_key": run_key,
                "repository": {
                    "repo_id": "repo-one", "repository_revision_key": terminal_key,
                    "state": "NO_RTL", "stage_status": {},
                },
                "designs": [],
            }))
            with ProcessingQueue(corpus) as queue:
                queue.enqueue(round_id, terminal_key, "/immutable/one")
                queue.enqueue(round_id, pending_key, "/immutable/two")
                self.assertIsNotNone(queue.claim(round_id, "worker"))
                queue.finish(
                    round_id, terminal_key, terminal_state="NO_RTL",
                    run_key=run_key, artifact_path=str(artifact),
                )
            terminal, repositories = phase2_round_delta.locked_processing_context(
                corpus, round_id, {terminal_key, pending_key},
            )
            self.assertEqual(terminal, {terminal_key})
            self.assertEqual(repositories, [])

    def test_scheduler_round_calibration_is_exactly_once_and_identity_bound(self):
        with tempfile.TemporaryDirectory() as directory, frontier.FrontierDB(Path(directory) / "frontier.sqlite") as db:
            report = {
                "factory_round_id": "p2r_exactly_once", "yield_status": "FINAL",
                "acquisition_cohort": {"new_acquired_revisions": 400},
                "provider_strategy_query_family": [{
                    "provider": "github", "strategy": "keyword", "query_family": "cpu",
                    "new_acquired_revisions": 10, "new_design_instances": 4,
                    "new_design_families": 3,
                }],
            }
            _, first = scheduler.calibrate_phase2_round(db, report, 400, "a" * 64)
            _, second = scheduler.calibrate_phase2_round(db, report, 400, "a" * 64)
            self.assertEqual(first, "CALIBRATED")
            self.assertEqual(second, "IDEMPOTENT_CACHE_HIT")
            row = db.connection.execute(
                "SELECT new_families FROM source_yield WHERE source_key='github:keyword:family:cpu'"
            ).fetchone()
            self.assertEqual(row[0], 3)
            with self.assertRaisesRegex(ValueError, "different FINAL delta"):
                scheduler.calibrate_phase2_round(db, report, 400, "b" * 64)

    def test_three_final_family_rounds_activate_precision_policy_for_batch4(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            with frontier.FrontierDB(corpus / "state/frontier.sqlite") as db:
                current = None
                for sequence in range(1, 4):
                    round_id = f"p2f_20260811_design-family-10k_batch{sequence:04d}"
                    report = {
                        "factory_round_id": round_id, "yield_status": "FINAL",
                        "acquisition_cohort": {"new_acquired_revisions": 400},
                        "provider_strategy_query_family": [],
                        "admission_anchor_yield": [{
                            "admission_anchor": "ORGANIZATION_ONLY",
                            "new_acquired_revisions": 200, "processed_revisions": 200,
                            "no_rtl_revisions": 198, "new_design_instances": 2,
                            "new_design_families": 1, "new_gold_families": 0,
                        }],
                    }
                    path = corpus / "quality/phase2/rounds" / round_id / "phase2_round_delta_summary.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(report))
                    current = report
                policy = scheduler.discovery_precision_recalibration(db, current)
                self.assertIsNotNone(policy)
                self.assertEqual(policy["activation_boundary"], "p2f_batch0004_and_later")
                self.assertEqual(policy["admission_anchor_cells"]["ORGANIZATION_ONLY"]["tier"], "DORMANT")

    def test_split_conflict_normalization_merges_overlapping_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            manifest = corpus / "manifests/split_assignments.jsonl"
            manifest.parent.mkdir(parents=True)
            groups = [f"sg_{index}" for index in range(8)]
            manifest.write_text("\n".join(json.dumps({
                "split_group_id": group,
                "split": "train" if index % 2 == 0 else "val",
            }) for index, group in enumerate(groups)) + "\n")
            conflicts = [
                {"old_split_groups": groups[0:2], "old_splits": ["train", "val"]},
                {"old_split_groups": groups[1:4], "old_splits": ["train", "val"]},
                {"old_split_groups": groups[3:6], "old_splits": ["train", "val"]},
                {"old_split_groups": groups[5:8], "old_splits": ["train", "val"]},
            ]
            components = target_controller.normalize_reconciliation_components(
                corpus, conflicts,
            )
            self.assertEqual(len(components), 1)
            self.assertEqual(components[0]["old_split_groups"], groups)
            self.assertEqual(components[0]["target_split"], "val")
            self.assertEqual(
                components[0]["authorization_scope"], "FULL_TRANSITIVE_COMPONENT",
            )

    def test_test_boundary_dominates_after_global_component_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            manifest = corpus / "manifests/split_assignments.jsonl"
            manifest.parent.mkdir(parents=True)
            rows = [
                {"split_group_id": "sg_train", "split": "train"},
                {"split_group_id": "sg_val", "split": "val"},
                {"split_group_id": "sg_test", "split": "test"},
            ]
            manifest.write_text("\n".join(map(json.dumps, rows)) + "\n")
            components = target_controller.normalize_reconciliation_components(
                corpus,
                [
                    {"old_split_groups": ["sg_train", "sg_val"], "old_splits": ["train", "val"]},
                    {"old_split_groups": ["sg_val", "sg_test"], "old_splits": ["val", "test"]},
                ],
            )
            self.assertEqual(len(components), 1)
            self.assertEqual(components[0]["old_splits"], ["test", "train", "val"])
            self.assertEqual(components[0]["target_split"], "test")


class AcquisitionTests(unittest.TestCase):
    def test_safe_archive_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.tar"
            with tarfile.open(archive, "w") as handle:
                good = tarfile.TarInfo("repo/rtl/top.v")
                body = b"module top; endmodule\n"
                good.size = len(body)
                handle.addfile(good, io.BytesIO(body))
                traversal = tarfile.TarInfo("repo/../../escaped")
                traversal.size = 3
                handle.addfile(traversal, io.BytesIO(b"bad"))
                link = tarfile.TarInfo("repo/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                handle.addfile(link)
            files, _ = acquisition.safe_extract_archive(archive, root / "out", 100, 10000)
            self.assertEqual(files, 1)
            self.assertTrue((root / "out" / "rtl" / "top.v").is_file())
            self.assertFalse((root / "escaped").exists())
            self.assertFalse((root / "out" / "link").exists())

    def test_archive_limits_terminate_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "large.tar"
            with tarfile.open(archive, "w") as handle:
                for index in range(2):
                    info = tarfile.TarInfo(f"repo/{index}.v")
                    info.size = 8
                    handle.addfile(info, io.BytesIO(b"12345678"))
            with self.assertRaisesRegex(RuntimeError, "EXTRACT_LIMIT_EXCEEDED"):
                acquisition.safe_extract_archive(archive, root / "out", 1, 100)

    def test_local_revision_snapshot_is_idempotent_and_never_executes_repo_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, corpus = root / "repo", root / "corpus"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/rtl.git"], cwd=repo, check=True)
            (repo / "top.v").write_text("module top; endmodule\n")
            (repo / "Makefile").write_text("all:\n\ttouch SHOULD_NOT_EXIST\n")
            (repo / ".gitmodules").write_text("[submodule \"ip\"]\n\tpath = ip\n\turl = git@github.com:example/dependency.git\n")
            subprocess.run(["git", "add", "top.v", "Makefile", ".gitmodules"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            args = SimpleNamespace(max_files=100, max_extract_bytes=100000)
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                first = acquisition.ingest_local_repository(db, corpus, repo, args)
                second = acquisition.ingest_local_repository(db, corpus, repo, args)
                self.assertEqual(first["state"], "ACQUIRED")
                self.assertEqual(second["state"], "CACHE_HIT")
                self.assertEqual(db.counts()["repository_revisions"], 1)
                self.assertEqual(db.counts()["repo_edges"], 1)
            self.assertFalse((repo / "SHOULD_NOT_EXIST").exists())

    def test_acquired_revision_intake_preserves_git_identity_without_dot_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, corpus = root / "repo", root / "corpus"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/identity.git"], cwd=repo, check=True)
            (repo / "top.v").write_text("module top; endmodule\n")
            subprocess.run(["git", "add", "top.v"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            args = SimpleNamespace(max_files=100, max_extract_bytes=100000)
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                result = acquisition.ingest_local_repository(db, corpus, repo, args)
            intake, count = factory.materialize_acquired_intake(corpus)
            self.assertEqual(count, 1)
            link = next(intake.iterdir())
            metadata = processor.git_metadata(link)
            self.assertEqual(metadata["commit_sha"], result["revision"])
            self.assertEqual(metadata["repository_url"], "https://github.com/example/identity")
            manifests = corpus / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "repositories.jsonl").write_text(json.dumps({
                "repository_url": metadata["repository_url"],
                "commit_sha": metadata["commit_sha"],
            }) + "\n")
            _, pending = factory.materialize_acquired_intake(corpus)
            self.assertEqual(pending, 0)
            self.assertEqual(list(intake.iterdir()), [])

    def test_abandoned_running_state_is_reconciled_without_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            active = corpus / "state" / "active"
            active.mkdir(parents=True)
            run_key = "a" * 64
            state_path = active / f"{run_key}.json"
            state_path.write_text(json.dumps({"run_key": run_key, "status": "RUNNING"}) + "\n")
            self.assertEqual(processor.reconcile_stale_active_states(corpus), 1)
            state = json.loads(state_path.read_text())
            self.assertEqual(state["status"], "INTERRUPTED_RECOVERABLE")
            self.assertEqual(state["reconcile_reason"], "WORKER_LOCK_RELEASED_BEFORE_COMMIT")

    def test_discovery_acquisition_processing_rerun_is_idempotent(self):
        yosys = Path("/opt/OpenROAD/oss-cad-suite/bin/yosys")
        if not yosys.exists():
            self.skipTest("Yosys unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root, repo, corpus = root / "sources", root / "sources" / "demo", root / "corpus"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/e2e.git"], cwd=repo, check=True)
            (repo / "top.v").write_text("module demo_top(input a, output y); assign y = a; endmodule\n")
            subprocess.run(["git", "add", "top.v"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            local_args = SimpleNamespace(max_files=100, max_extract_bytes=100000)
            with frontier.FrontierDB(frontier.default_frontier_path(corpus)) as db:
                acquisition.ingest_local_repository(db, corpus, repo, local_args)
            command = [sys.executable, str(SCRIPTS / "run_expansion_round.py"), "--source-root", str(source_root), "--corpus-root", str(corpus), "--max-repos", "1", "--synthesize"]
            first = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            dry = subprocess.run(command + ["--dry-run"], text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(json.loads(dry.stdout)["candidate_count"], 0)
            (corpus / "manifests" / "repositories.jsonl").write_text("")
            unpublished = subprocess.run(command + ["--dry-run"], text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(json.loads(unpublished.stdout)["candidate_count"], 1)
            resumed = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False)
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            rows = [json.loads(line) for line in (corpus / "manifests" / "all_designs.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"]["source_storage"], "IMMUTABLE_REPOSITORY_REVISION")
            self.assertIn("repository_revision_key", rows[0]["source"])
            self.assertFalse((corpus / "original_rtl" / rows[0]["design_id"]).exists())
            layout = subprocess.run([sys.executable, str(SCRIPTS / "materialize_storage_layout.py"), "--corpus-root", str(corpus), "--archive-linked-legacy"], text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(layout.returncode, 0, layout.stderr + layout.stdout)
            refreshed = json.loads((corpus / "manifests" / "all_designs.jsonl").read_text().splitlines()[0])
            self.assertEqual(refreshed["storage"]["storage_schema"], "rtl_storage_layout_v2")
            self.assertRegex(Path(refreshed["storage"]["design_path"]).name, r"e2e__demo-top__[0-9a-f]{16}")
            self.assertTrue(Path(refreshed["storage"]["design_path"], "design.json").is_file())

    def test_display_name_uses_canonical_repo_not_intake_symlink(self):
        record = {
            "design_id": "d_0edbdabf42460a620b0e",
            "identity": {"repository_name": "github__example__airisc_core__deadbeef"},
            "provenance": {"repository_url": "https://github.com/example/airisc_core.git"},
            "build": {"top_module": "airi5c_core"},
        }
        self.assertEqual(
            storage_layout.design_display_name(record),
            "airisc-core__airi5c-core__0edbdabf42460a62",
        )

    def test_empty_benchmark_namespace_does_not_unlock_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "verilog_eval").mkdir()
            (root / "verilog_eval" / "registry.json").write_text('{"status":"EMPTY"}\n')
            hashes, ready = processor.load_benchmark_hashes(root)
            self.assertFalse(ready)
            self.assertEqual(hashes, set())


if __name__ == "__main__":
    unittest.main()
