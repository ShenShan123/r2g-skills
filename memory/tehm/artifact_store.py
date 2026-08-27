"""Content-addressed artifact store (design doc 19.8).

Heavy artifacts (VCD, RTL slices, Yosys JSON, reports, diffs) live outside the
DB as ``sha256/<hh>/<digest>`` files; the DB keeps only digests + references.
Layout:
    artifacts/
      sha256/
        00/00<digest...>
        01/...
      manifests/            # manifest JSON keyed by digest (design doc 19.8)
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from tehm.ids import stable_dumps

ARTIFACT_KINDS = frozenset({
    "vcd", "rtl", "ast", "yosys_json", "report", "counterexample", "diff",
    "graph", "patch", "config", "predicate_snapshot", "failure_signature",
})

SCHEMA_VERSION = "artifact-v1"


class ArtifactStore:
    """Content-addressed artifact store rooted at ``root`` (default <memory>/artifacts)."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else None
        self._blobs = self.root / "sha256" if self.root else None
        self._manifests = self.root / "manifests" if self.root else None

    def _require_root(self) -> None:
        if self.root is None:
            raise RuntimeError("ArtifactStore has no root (was it configured?)")

    def put(self, kind: str, data: bytes, *, producer: str = "tehm") -> dict:
        """Store ``data`` and return its manifest (digest + metadata).

        Idempotent: identical bytes land at the same path (overwrite is a no-op
        because the content hash is the address).
        """
        self._require_root()
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unknown artifact kind {kind!r}; allowed: {sorted(ARTIFACT_KINDS)}")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        rel = self._digest_path(digest)
        dest = self._blobs / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        manifest = {
            "digest": digest,
            "kind": kind,
            "producer": producer,
            "schema_version": SCHEMA_VERSION,
            "size": len(data),
            "relative_path": f"sha256/{rel}",
        }
        self._write_manifest(digest, manifest)
        return manifest

    def put_file(self, kind: str, src: Path, *, producer: str = "tehm") -> dict:
        return self.put(kind, src.read_bytes(), producer=producer)

    def put_json(self, kind: str, obj: Any, *, producer: str = "tehm") -> dict:
        return self.put(kind, stable_dumps(obj).encode(), producer=producer)

    def get(self, manifest: dict) -> bytes:
        """Read bytes back for a manifest returned by ``put``/``put_json``."""
        self._require_root()
        digest = manifest["digest"]
        rel = self._digest_path(digest)
        path = self._blobs / rel
        if not path.exists():
            raise FileNotFoundError(f"artifact blob missing for {digest}")
        return path.read_bytes()

    def verify(self, manifest: dict) -> bool:
        """Re-hash the stored blob and confirm it matches the manifest digest."""
        try:
            actual = hashlib.sha256(self.get(manifest)).hexdigest()
            return manifest["digest"] == f"sha256:{actual}"
        except (FileNotFoundError, KeyError, TypeError):
            return False

    def resolve(self, ref: dict) -> bytes | None:
        """Resolve a manifest-or-ref dict (design doc artifacts are refs by digest)."""
        if isinstance(ref, dict) and "digest" in ref:
            return self.get(ref)
        return None

    # -- internals ----------------------------------------------------------

    def _digest_path(self, digest: str) -> str:
        """``sha256:ab...`` -> ``ab/ab...`` (2-hex shard dir + full hex)."""
        hexed = digest.split(":", 1)[1]
        return f"{hexed[:2]}/{hexed}"

    def _write_manifest(self, digest: str, manifest: dict) -> None:
        if not self._manifests:
            return
        self._manifests.mkdir(parents=True, exist_ok=True)
        (self._manifests / f"{digest.split(':', 1)[1]}.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def delete_root(self) -> None:
        """Remove the whole store tree (used by tests / rebuild)."""
        if self.root and self.root.exists():
            shutil.rmtree(self.root)
