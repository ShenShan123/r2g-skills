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


if __name__ == "__main__":
    unittest.main()
