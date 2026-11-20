"""Focused checks for accounting semantics, call caps and converter freezing."""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

from native_proxy import Ledger, usage_record
from runner_native import build_container_command, freeze_converter
from campaign import ARMS, make_schedule


class NativeRunnerTest(unittest.TestCase):
    def test_four_arms_start_with_disjoint_cpu_allocations(self):
        jobs = make_schedule(list(range(32)), 20261115)
        first = jobs[:4]
        self.assertEqual(set(ARMS), {j["arm"] for j in first})
        self.assertEqual(set(range(4)), {j["slot"] for j in first})
        self.assertEqual(32, len({c for j in first for c in j["cpuset"].split(",")}))
        for arm in ARMS:
            repeats = [j for j in jobs if j["arm"] == arm]
            self.assertEqual([1, 2, 3], [j["repeat"] for j in repeats])
            self.assertEqual(1, len({j["cpuset"] for j in repeats}))

    def test_parallel_reservations_enforce_total_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "usage.json", "model", 5)
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                rows = list(pool.map(lambda i: ledger.reserve({"index": i}), range(30)))
            self.assertEqual(5, sum(row is not None for row in rows))
            self.assertEqual(list(range(1, 6)), [row["call_index"] for row in ledger.snapshot()])

    def test_usage_includes_cache_writes_and_is_not_complete_without_terminal_event(self):
        events = [{"type": "message_start", "message": {"model": "ds", "usage": {"input_tokens": 10, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3}}},
                  {"type": "message_delta", "usage": {"output_tokens": 9}}]
        value = usage_record(events, "anthropic")
        self.assertEqual((20, 13, 29), (value["input_tokens"], value["uncached_input_tokens"], value["total_tokens"]))
        self.assertFalse(value["stream_complete"])
        self.assertTrue(usage_record([*events, {"type": "message_stop"}], "anthropic")["stream_complete"])
        self.assertFalse(usage_record([], "openai-responses")["usage_reported"])

    def test_converter_archive_is_reproducible_and_rejects_external_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "run.sh").write_text("#!/bin/bash\nexit 0\n")
            first, second = root / "one", root / "two"
            first.mkdir(); second.mkdir()
            self.assertEqual(freeze_converter(source, first)["converter_sha256"], freeze_converter(source, second)["converter_sha256"])
            (source / "host-library").symlink_to("/etc/passwd")
            self.assertFalse(freeze_converter(source, root)["converter_present"])

    def test_native_mounts_do_not_expose_agent_or_private_runtime(self):
        cfg = {"harness": "codex", "model": "gpt", "protocol": "openai-responses", "native_bins": "/bin-root", "dev_root": "/raw-dev", "public_root": "/public"}
        command = build_container_command(cfg, Path("/attempt"), "test")
        self.assertIn("--network=none", command)
        self.assertIn("/attempt/tmp:/tmp:rw", command)
        self.assertFalse(any("/tmp:rw,exec,size=" in part for part in command))
        self.assertFalse(any(":/agent:" in part or ":/r2g:" in part for part in command))
        self.assertFalse(any("/var/run" in part for part in command))

    def test_bubblewrap_native_uses_only_explicit_mounts_and_isolated_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "rootfs"
            image.mkdir()
            (image.parent / "image.json").write_text(json.dumps({"Config": {"Env": ["PATH=/usr/bin:/bin"]}}))
            cfg = {"harness": "codex", "model": "gpt", "protocol": "openai-responses",
                   "native_bins": "/bin-root", "dev_root": "/raw-dev", "public_root": "/public",
                   "image_root": str(image)}
            command = build_container_command(cfg, Path("/attempt"), "test", version_only=True)
            self.assertEqual(command[0], "bwrap")
            self.assertIn("--unshare-all", command)
            self.assertIn("--clearenv", command)
            self.assertNotIn("/r2g", command)
            self.assertNotIn("/agent", command)
            self.assertNotIn("--dev-bind", command)
            self.assertEqual(command[-1], "--version-only")


if __name__ == "__main__":
    unittest.main()
