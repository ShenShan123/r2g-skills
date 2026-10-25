"""Joint Rewrite Anti-Unification (design doc 6.6, 23.2, 26 Phase 5).

Given the role-normalized rewrites of an effect group, produce the most specific
common generalization with shared holes + crystallization-time witnesses.

Invariants (design doc 23.3, 6.6):
  1. before/after share ONE hole namespace (a global hole counter; a path in the
     before and a path in the after can never collide).
  2. crystallization-time witnesses are preserved: every hole records the exact
     value each source episode substituted for it (``source_substitutions``).
  3. deterministic: fixed input set + algorithm version => identical output
     (holes named by a global counter; pairwise-merge order fixed by cost, then
     episode id).
  4. the full merge trace is retained.

Merge order (design doc 6.6):
  pairwise AU cost (number of holes a merge would create) -> lowest cost pair ->
  tie-break by (min episode id, min transition id) -> merge -> recompute.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tehm.ids import is_hole, stable_dumps

ALGORITHM_VERSION = "joint-au-v0.1"

_MISSING = object()


@dataclass(frozen=True)
class AntiUnifyConfig:
    algorithm_version: str = ALGORITHM_VERSION
    min_group_size: int = 2


@dataclass(frozen=True)
class MergeStep:
    pair: tuple
    cost: int
    created_holes: list

    def to_dict(self) -> dict:
        return {"pair": list(self.pair), "cost": self.cost,
                "created_holes": self.created_holes}


@dataclass
class AntiUnifyResult:
    before_pattern: dict
    after_pattern: dict
    source_substitutions: dict
    hole_constraints: dict
    abstraction_metrics: dict
    merge_trace: list
    algorithm_version: str

    def to_dict(self) -> dict:
        return {
            "before_pattern": self.before_pattern,
            "after_pattern": self.after_pattern,
            "source_substitutions": self.source_substitutions,
            "hole_constraints": self.hole_constraints,
            "abstraction_metrics": self.abstraction_metrics,
            "merge_trace": [s.to_dict() for s in self.merge_trace],
            "algorithm_version": self.algorithm_version,
        }


def anti_unify_rewrites(examples: list, config: AntiUnifyConfig | None = None
                        ) -> AntiUnifyResult:
    """Anti-unify one effect group's role-normalized rewrites.

    ``examples``: sequence of ``RoleNormalizedRewrite`` sharing an effect key.
    Raises if fewer than ``min_group_size`` examples (no rule from a singleton).
    """
    config = config or AntiUnifyConfig()
    if len(examples) < config.min_group_size:
        raise ValueError(
            f"anti-unification needs >= {config.min_group_size} examples, got "
            f"{len(examples)}")

    # Each tree tracks its sources and the ORIGINAL per-source slot values.
    trees = [_Tree.from_example(ex) for ex in examples]
    source_values: dict[str, dict] = {}   # transition_id -> {hole: value}
    for ex in examples:
        source_values[ex.transition_id] = {}
    hole_counter = [0]
    merge_trace: list[MergeStep] = []

    while len(trees) > 1:
        best: tuple | None = None
        best_cost = 10 ** 9
        for i in range(len(trees)):
            for j in range(i + 1, len(trees)):
                cost = _merge_cost(trees[i], trees[j])
                key = _pair_key(trees[i], trees[j])
                if best is None or (cost, key) < (best_cost, best[2]):
                    best = (i, j, key)
                    best_cost = cost
        i, j, _ = best
        a, b = trees[i], trees[j]
        merged, new_holes = _merge(a, b, hole_counter, source_values)
        merge_trace.append(MergeStep(
            pair=_pair_tuple(a, b), cost=best_cost, created_holes=new_holes))
        # Replace a,b with the merged tree (drop b, overwrite a).
        trees[j] = merged
        del trees[i]

    final = trees[0]
    before_pattern, after_pattern = _split_patterns(final.slots)
    hole_constraints = _hole_constraints(final.slots, source_values)
    metrics = _abstraction_metrics(examples, final.slots, len(source_values))
    return AntiUnifyResult(
        before_pattern=before_pattern,
        after_pattern=after_pattern,
        source_substitutions=source_values,
        hole_constraints=hole_constraints,
        abstraction_metrics=metrics,
        merge_trace=merge_trace,
        algorithm_version=config.algorithm_version,
    )


# -- internal tree ------------------------------------------------------------

class _Tree:
    __slots__ = ("sources", "slots", "original")

    def __init__(self, sources: list, slots: dict, original: dict):
        self.sources = sources          # [transition_id...]
        self.slots = slots              # {path: concrete | "$Hn"}
        self.original = original        # {transition_id: {path: value}}

    @classmethod
    def from_example(cls, ex) -> "_Tree":
        slots = ex.slot_dict()
        return cls([ex.transition_id], slots, {ex.transition_id: dict(slots)})


_is_hole = is_hole  # internal alias used by the merge internals


def _merge_cost(a: _Tree, b: _Tree) -> int:
    """Number of NEW holes a merge of a and b would create.

    Merging a hole with a concrete value (or another hole) absorbs — it creates
    no new hole — so the cost is the count of concrete-vs-concrete divergences.
    """
    count = 0
    for path in sorted(set(a.slots) | set(b.slots)):
        va = a.slots.get(path, _MISSING)
        vb = b.slots.get(path, _MISSING)
        if va == vb and va is not _MISSING:
            continue
        if _is_hole(va) or _is_hole(vb):
            continue  # absorption, no new hole
        count += 1
    return count


def _merge(a: _Tree, b: _Tree, hole_counter: list, source_values: dict
           ) -> tuple[_Tree, list]:
    """Merge two trees with hole-absorption.

    One hole per slot path: the first concrete-vs-concrete divergence creates a
    hole; later merges absorb the other side's witnesses into that same hole so
    ``source_substitutions`` contains exactly the FINAL rule's holes.
    """
    merged_sources = a.sources + b.sources
    merged_original = dict(a.original)
    merged_original.update(b.original)
    new_slots: dict = {}
    new_holes: list = []
    for path in sorted(set(a.slots) | set(b.slots)):
        va = a.slots.get(path, _MISSING)
        vb = b.slots.get(path, _MISSING)
        if va == vb and va is not _MISSING:
            new_slots[path] = va
            continue
        a_hole, b_hole = _is_hole(va), _is_hole(vb)
        if a_hole and b_hole:
            new_slots[path] = va           # merge hole identities: absorb b's
            for source in b.sources:       # witnesses into a's hole
                source_values[source][va] = source_values[source].get(
                    vb, merged_original[source].get(path))
            continue
        if a_hole:
            new_slots[path] = va           # absorb concrete b into existing hole
            for source in b.sources:
                source_values[source][va] = merged_original[source].get(path)
            continue
        if b_hole:
            new_slots[path] = vb           # absorb concrete a into existing hole
            for source in a.sources:
                source_values[source][vb] = merged_original[source].get(path)
            continue
        # both concrete and differ -> create a hole with all sources as witnesses
        hole = f"$H{hole_counter[0]}"
        hole_counter[0] += 1
        new_slots[path] = hole
        new_holes.append(hole)
        for source in merged_sources:
            source_values[source][hole] = merged_original[source].get(path)
    return _Tree(merged_sources, new_slots, merged_original), new_holes


def _pair_key(a: _Tree, b: _Tree) -> tuple:
    """Deterministic pair key for tie-breaking (min episode? we only have
    transition ids on trees; use min/max transition id)."""
    return (min(min(a.sources), min(b.sources)), max(min(a.sources), min(b.sources)))


def _pair_tuple(a: _Tree, b: _Tree) -> tuple:
    ids = sorted(a.sources + b.sources)
    return (ids[0], ids[-1])


# -- output shaping -----------------------------------------------------------

def _split_patterns(slots: dict) -> tuple[dict, dict]:
    """before_pattern = match.* (L); after_pattern = rewrite/execution/verification (R)."""
    before: dict = {}
    after: dict = {}
    for path, value in sorted(slots.items()):
        if path.startswith("match."):
            before[path[len("match."):]] = value
        else:
            after[path] = value
    return before, after


def _hole_constraints(slots: dict, source_values: dict) -> dict:
    """For each hole: the observed value set across sources (constraint)."""
    constraints: dict = {}
    for path, value in sorted(slots.items()):
        if isinstance(value, str) and value.startswith("$H"):
            observed = sorted({str(source_values[s].get(value))
                               for s in source_values})
            constraints[value] = {"path": path, "observed_values": observed}
    return constraints


def _abstraction_metrics(examples: list, slots: dict, num_sources: int) -> dict:
    n_slots = max(len(slots), 1)
    n_holes = sum(1 for v in slots.values()
                  if isinstance(v, str) and v.startswith("$H"))
    return {
        "num_sources": num_sources,
        "num_episodes": len({ex.episode_id for ex in examples}),
        "num_lineages": len({ex.lineage_id for ex in examples
                             if ex.lineage_id}),
        "num_slots": len(slots),
        "num_holes": n_holes,
        "hole_ratio": n_holes / n_slots,
        "structural_retention": 1.0 - n_holes / n_slots,
        "abstraction_coverage": n_holes / n_slots,
    }


def result_digest(result: AntiUnifyResult) -> str:
    payload = stable_dumps({
        "before": result.before_pattern,
        "after": result.after_pattern,
        "algorithm_version": result.algorithm_version,
    })
    return f"au_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"
