"""Storage policy for reproducible ORFS campaign work roots."""
from __future__ import annotations

import os
from pathlib import Path


DATA1_CAMPAIGN_ROOT = Path("/data1/zhangdy/tehm-campaigns").resolve()
SCRATCH_ROOT = Path(os.environ.get("R2G_ORFS_SCRATCH_ROOT", "/tmp/tehm-orfs")).resolve()


def default_work_root(name: str) -> Path:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"invalid ORFS campaign name: {name!r}")
    return SCRATCH_ROOT / name


def enforce_work_root(root: Path) -> Path:
    """Refuse accidental regenerable writes under the nearly-full data disk."""
    root = Path(root).resolve()
    try:
        root.relative_to(DATA1_CAMPAIGN_ROOT)
    except ValueError:
        return root
    if os.environ.get("R2G_ALLOW_DATA1_ORFS_WORK") == "1":
        return root
    raise RuntimeError(
        f"refusing regenerable ORFS work root under {DATA1_CAMPAIGN_ROOT}: {root}; "
        "use /tmp (default) or set R2G_ALLOW_DATA1_ORFS_WORK=1 for an intentional replay")


def storage_policy(root: Path, *, evidence_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    return {
        "scratch_root": str(root),
        "reusable_work_globs": ["backend/RUN_*", ".orfs-work", "logs", "results", "objects"],
        "retained_evidence": [
            "campaign_manifest.json", "campaign_state.json", "reports/",
            "features/", "final/", "drc/", "lvs/", "rcx/", "stage_log.jsonl",
            "run-meta.json",
        ],
        "evidence_root": str(evidence_root.resolve()) if evidence_root else None,
        "policy": "reproducible RUN/logs/results/objects stay on scratch; copy only receipts/reports/DEF/summary to evidence root",
    }
