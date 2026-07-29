from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_expansion_round import select_retry_candidates  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "source_group",
        "design",
        "priority",
        "expected_top",
        "source_path",
        "rtl_files",
        "include_dirs",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class RetryScopeTests(unittest.TestCase):
    def test_auto_fix_retry_is_scoped_to_current_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate = root / "candidate.csv"
            retry = root / "retry.csv"
            auto = root / "auto.csv"
            scoped = root / "scoped.csv"
            write_csv(
                candidate,
                [
                    {
                        "source_group": "downloads",
                        "design": "current",
                        "priority": "medium",
                        "expected_top": "current",
                        "source_path": "/src/current.v",
                    }
                ],
            )
            write_csv(retry, [{"design": "historical", "source_path": "/src/historical.v"}])
            write_csv(
                auto,
                [
                    {"design": "historical", "source_path": "/src/historical.v"},
                    {"design": "current", "source_path": "/src/current.v"},
                ],
            )
            selected = select_retry_candidates(
                candidate_csv=candidate,
                retry_candidates_csv=retry,
                auto_fix_retry_csv=auto,
                scoped_out_csv=scoped,
                retry_scope="scoped",
            )
            self.assertEqual(selected, scoped)
            with scoped.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["design"] for row in rows], ["current"])


class RetryFieldPreservationTests(unittest.TestCase):
    """Held-out V3 frontend cohort, "retry parameter loss".

    `build_scoped_retry_candidates` wrote a fixed 8-column row, so every
    execution-affecting field auto_fix_failures had just computed was dropped:
    a retry generated as `synth_memory_max_bits=131072` re-ran at the stock 4096
    cap and reproduced the identical memory-limit failure, forever.
    """

    def _write_any(self, path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_auto_fix_execution_fields_survive_the_scoped_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate, auto, scoped = root / "c.csv", root / "a.csv", root / "s.csv"
            write_csv(candidate, [{
                "source_group": "downloads", "design": "mor1kx", "priority": "high",
                "expected_top": "mor1kx", "source_path": "/src/mor1kx.v",
                "rtl_files": "/src/mor1kx.v", "include_dirs": "/src", "notes": "",
            }])
            self._write_any(auto, [
                "design", "source_path", "rtl_files", "include_dirs", "notes",
                "synth_variant", "synth_memory_max_bits", "synth_frontend",
                "resource_tier", "top_parameters",
            ], [{
                "design": "mor1kx", "source_path": "/src/mor1kx.v",
                "rtl_files": "/src/mor1kx.v", "include_dirs": "/src",
                "notes": "auto_fix:raise_synth_memory_max_bits:131072",
                "synth_variant": "yosys_abc_area1", "synth_memory_max_bits": "131072",
                "synth_frontend": "slang", "resource_tier": "high",
                "top_parameters": "WIDTH=32",
            }])
            out = select_retry_candidates(
                candidate_csv=candidate, retry_candidates_csv=auto,
                auto_fix_retry_csv=auto, scoped_out_csv=scoped, retry_scope="scoped")
            self.assertEqual(out, scoped)
            with scoped.open(newline="", encoding="utf-8") as handle:
                row = list(csv.DictReader(handle))[0]
            self.assertEqual(row["synth_memory_max_bits"], "131072")
            self.assertEqual(row["synth_frontend"], "slang")
            self.assertEqual(row["synth_variant"], "yosys_abc_area1")
            self.assertEqual(row["resource_tier"], "high")
            self.assertEqual(row["top_parameters"], "WIDTH=32")

    def test_original_values_fill_in_where_the_retry_row_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candidate, auto, scoped = root / "c.csv", root / "a.csv", root / "s.csv"
            self._write_any(candidate, [
                "source_group", "design", "priority", "expected_top", "source_path",
                "rtl_files", "include_dirs", "notes", "synth_variant",
            ], [{
                "source_group": "downloads", "design": "jpeg", "priority": "high",
                "expected_top": "jpeg_core", "source_path": "/src/jpeg.v",
                "rtl_files": "/src/jpeg.v", "include_dirs": "/src", "notes": "",
                "synth_variant": "yosys_abc_area0",
            }])
            self._write_any(auto, ["design", "source_path", "synth_memory_max_bits"],
                            [{"design": "jpeg", "source_path": "/src/jpeg.v",
                              "synth_memory_max_bits": "65536"}])
            select_retry_candidates(
                candidate_csv=candidate, retry_candidates_csv=auto,
                auto_fix_retry_csv=auto, scoped_out_csv=scoped, retry_scope="scoped")
            with scoped.open(newline="", encoding="utf-8") as handle:
                row = list(csv.DictReader(handle))[0]
            self.assertEqual(row["synth_memory_max_bits"], "65536")   # from retry
            self.assertEqual(row["synth_variant"], "yosys_abc_area0")  # from original
            self.assertEqual(row["rtl_files"], "/src/jpeg.v")          # from original


if __name__ == "__main__":
    unittest.main()
