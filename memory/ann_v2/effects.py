"""ann-v2 step effect, new failures and the positive gate (spec §5).

Computed only from the BOUND pre- and post-action runs. An unmeasured check is UNKNOWN,
never "no failure"; a check that was not measured before the action cannot be judged as
new and is recorded NOT_MEASURED_BEFORE.
"""
from __future__ import annotations

from ann_v2.fields import CHECKS, STAGE_ORDER

EFFECTS = ("CLEARED", "IMPROVED", "NO_CHANGE", "REGRESSED", "UNKNOWN")


def _v(meas: dict, check: str, field: str):
    f = (meas.get(check) or {}).get("fields", {}).get(field) or {}
    return f.get("value") if f.get("status") == "KNOWN" else None


def _compare(before, after) -> str:
    """Lower is better; 0 is clean."""
    if before is None or after is None:
        return "UNKNOWN"
    if after == 0:
        return "CLEARED"
    return "IMPROVED" if after < before else "NO_CHANGE" if after == before else "REGRESSED"


def _rank(stage) -> int | None:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else None


def step_effect(target: dict, pre: dict | None, post: dict | None) -> str:
    """Effect of the action on one typed target failure."""
    kind = target["type"]
    if pre is None or post is None or target.get("type_status") != "KNOWN" or kind == "UNKNOWN":
        return "UNKNOWN"
    if kind == "stage_abort":
        stage = target["fields"]["stage"]["value"]
        post_stage = post["stage"]
        if not post_stage["measured"]:
            return "UNKNOWN"
        passed = _v(post, "stage", "passed") or []
        if stage in passed:
            return "CLEARED"                       # a later abort is a NEW failure, not this one
        if post_stage["present"]:
            at = _v(post, "stage", "stage")
            if at == stage and stage == "route":
                # labels v2: the error code and the GRT overflow decide, not the stage alone
                code0, code1 = target["fields"]["error_code"].get("value"), _v(post, "stage", "error_code")
                if code0 is not None and code0 == code1:
                    o0, o1 = _v(pre, "stage", "grt_overflow"), _v(post, "stage", "grt_overflow")
                    if o0 is None or o1 is None or o1 == o0:
                        return "NO_CHANGE"
                    return "IMPROVED" if o1 < o0 else "REGRESSED"
                if str(code0 or "").startswith("GRT-") and _v(post, "stage", "global_route_clean") is True:
                    return "CLEARED"               # congestion gone; the later failure is a NEW one
                return "UNKNOWN" if code0 != code1 else "NO_CHANGE"
            if at == stage:
                return "NO_CHANGE"
            if _rank(at) is not None and _rank(stage) is not None and _rank(at) < _rank(stage):
                return "REGRESSED"
        return "UNKNOWN"
    if kind in ("drc", "route_residual"):
        if not post[kind]["measured"]:
            return "UNKNOWN"
        return _compare(_v(pre, kind, "count"), _v(post, kind, "count"))
    if kind == "lvs":
        if not post["lvs"]["measured"]:
            return "UNKNOWN"
        return "CLEARED" if post["lvs"]["present"] is False else "NO_CHANGE"
    if kind in ("setup", "hold"):
        if not post[kind]["measured"]:
            return "UNKNOWN"
        u0, u1 = _v(pre, kind, "unit"), _v(post, kind, "unit")
        b, a = _v(pre, kind, "value"), _v(post, kind, "value")
        if u0 is None or u0 != u1 or b is None or a is None:
            return "UNKNOWN"
        if a >= 0:
            return "CLEARED"
        return "IMPROVED" if a > b else "NO_CHANGE" if a == b else "REGRESSED"
    if kind == "drv":
        if not post["drv"]["measured"]:
            return "UNKNOWN"
        subs = ("max_slew", "max_cap", "max_fanout")
        b = [_v(pre, "drv", s) for s in subs]
        a = [_v(post, "drv", s) for s in subs]
        if any((x is None) != (y is None) for x, y in zip(b, a)) or all(x is None for x in a):
            return "UNKNOWN"
        return _compare(sum(x for x in b if x is not None), sum(y for y in a if y is not None))
    return "UNKNOWN"


def new_failures(pre: dict | None, post: dict | None) -> dict:
    """Per check: KNOWN-none, KNOWN-present, UNKNOWN or NOT_MEASURED_BEFORE."""
    out = {}
    for check in CHECKS:
        if pre is None or post is None:
            out[check] = "UNKNOWN"
            continue
        b, a = pre[check], post[check]
        if not b["measured"]:
            out[check] = "NOT_MEASURED_BEFORE"
        elif not a["measured"]:
            out[check] = "UNKNOWN"
        elif check == "stage":
            same = b["present"] and _v(pre, "stage", "stage") == _v(post, "stage", "stage") \
                and _v(pre, "stage", "error_code") == _v(post, "stage", "error_code")
            out[check] = "KNOWN-present" if a["present"] and not same else "KNOWN-none"
        else:
            out[check] = "KNOWN-present" if a["present"] and not b["present"] else "KNOWN-none"
    return out


def positive(binding: dict, targets: list, effects: dict, new: dict, rolled_back) -> tuple[bool, list]:
    """(positive, blockers). Positive single-step evidence needs every condition of spec §5."""
    blockers = []
    if binding["post"]["status"] != "KNOWN":
        blockers.append("binding_post_unknown")
    if binding["pre"]["status"] != "KNOWN":
        blockers.append("binding_pre_unknown")
    if not targets or any(t.get("type_status") != "KNOWN" for t in targets):
        blockers.append("target_unknown")
    blockers += [f"effect:{k}={v}" for k, v in sorted(effects.items()) if v != "CLEARED"]
    blockers += [f"new_failure:{k}={v}" for k, v in sorted(new.items()) if v in ("KNOWN-present", "UNKNOWN")]
    if rolled_back is True:
        blockers.append("rolled_back")
    elif rolled_back is not False:
        blockers.append("rollback_unknown")
    return (not blockers), blockers


CANDIDATE_CLASS = "effect_cleared_rollback_unknown"


def candidate_class(binding: dict, targets: list, effects: dict, new: dict, rolled_back) -> str | None:
    """User decision D8 (2026-10-17): a unit whose target CLEARED on both bound runs with no new
    failure, but whose rollback is UNKNOWN, is a CANDIDATE experience -- never positive, never
    counted toward the category-A support threshold. Any arm that is shown such units sees the
    same content with this same label."""
    ok = (binding["post"]["status"] == "KNOWN" and binding["pre"]["status"] == "KNOWN"
          and bool(targets) and all(t.get("type_status") == "KNOWN" for t in targets)
          and bool(effects) and all(v == "CLEARED" for v in effects.values())
          and not any(v in ("KNOWN-present", "UNKNOWN") for v in new.values()))
    return CANDIDATE_CLASS if ok and rolled_back is None else None
