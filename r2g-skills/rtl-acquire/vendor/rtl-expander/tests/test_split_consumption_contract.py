#!/usr/bin/env python3

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import split_consumption_contract as contract


class SplitConsumptionContractTests(unittest.TestCase):
    def corpus(self, root: Path) -> Path:
        corpus = root / "corpus"
        (corpus / "manifests").mkdir(parents=True)
        (corpus / "state/controllers/design-family-10k").mkdir(parents=True)
        (corpus / "ledger").mkdir(parents=True)
        (corpus / "snapshots").mkdir(parents=True)
        (corpus / "manifests/split_profiles.jsonl").write_text(json.dumps({
            "schema": "rtl_split_profile_v1", "profile_id": "rtl_split_profile_v2",
            "split_schema": "rtl_split_v2", "split_epoch": "campaign-v2", "status": "CURRENT",
        }) + "\n")
        (corpus / "state/controllers/design-family-10k/controller.json").write_text(json.dumps({
            "schema": "rtl_design_family_target_controller_v1",
            "objective_id": "design-family-10k", "created_at": "2026-01-01T00:00:00+00:00",
            "target": 10000, "primary_metric": "formal_families",
            "hard_completion_target": {"metric": "formal_families"},
            "quality_and_completion_gates": "UNCHANGED", "heartbeat_at": "mutable",
        }))
        connection = sqlite3.connect(corpus / "state/corpus.sqlite")
        connection.execute(
            "CREATE TABLE designs(design_id TEXT,content_sha256 TEXT,family_id TEXT,split_group_id TEXT)"
        )
        connection.commit()
        connection.close()
        return corpus

    def test_explicit_contract_hash_lineage_and_final_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = self.corpus(Path(directory))
            created = contract.create_contract(corpus, "design-family-10k", "unit-test-user-confirmation")
            self.assertEqual(created["consumption_state"], contract.INTERNAL)
            _, _, state = contract.load_and_validate(
                corpus, "design-family-10k", allowed_states={contract.INTERNAL},
            )
            self.assertFalse(state["external_training_allowed"])
            metadata = contract.snapshot_metadata(corpus)
            self.assertFalse(metadata["external_training_eligible"])

            profiles = [
                {
                    "schema": "rtl_split_profile_v1", "profile_id": "rtl_split_profile_v2",
                    "split_schema": "rtl_split_v2", "split_epoch": "campaign-v2",
                    "status": "SUPERSEDED", "superseded_by": "rtl_split_profile_v3",
                },
                {
                    "schema": "rtl_split_profile_v1", "profile_id": "rtl_split_profile_v3",
                    "split_schema": "rtl_split_v3", "split_epoch": "campaign-v3",
                    "status": "CURRENT", "supersedes": "rtl_split_profile_v2",
                },
            ]
            (corpus / "manifests/split_profiles.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in profiles)
            )
            contract.load_and_validate(corpus, "design-family-10k", allowed_states={contract.INTERNAL})
            final = contract.transition_state(
                corpus, "design-family-10k", contract.FINAL_FROZEN,
                {"certified_internal_candidate_snapshot_id": "candidate"},
            )
            self.assertTrue(final["external_training_allowed"])
            self.assertTrue(contract.snapshot_metadata(corpus)["external_evaluation_eligible"])

    def test_contract_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = self.corpus(Path(directory))
            contract.create_contract(corpus, "design-family-10k", "unit-test-user-confirmation")
            path, _ = contract.contract_paths(corpus, "design-family-10k")
            payload = json.loads(path.read_text())
            payload["external_training_allowed"] = True
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(RuntimeError, "hash/schema"):
                contract.load_and_validate(corpus, "design-family-10k")


if __name__ == "__main__":
    unittest.main()
