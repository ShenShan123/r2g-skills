"""T2a per-task constraint and area envelope (contract section 3.3), acceptance helper.

A task manifest (JSON) freezes, identically for every arm and repeat:

    {"task": "<design>/<variant>", "platform": ..., "initial_config": {knob: value, ...},
     "immutable": {"knobs": [...], "files": {relative_path: "sha256:<hex>"}},
     "allowlist": {KNOB: {"type": "number", "min": a, "max": b, "max_step": d} |
                         {"type": "enum", "values": [...]}},
     "area": {"stdcell": {"ref": A, "ref_stage": "...", "max_rel_increase": r},
              "core":    {...}, "die": {...}}}

``max_rel_increase`` is a fraction (0.25 = +25 %); ``null`` declares the metric explicitly unconstrained (it is still
measured and reported). The values are frozen before any TEST repair run; this module only enforces them.

Only flat literal project configs can be read here; unresolved Make syntax fails closed. This is NOT a complete
Make/ORFS effective-config resolver, and execution-bound derived settings still need separate evidence. The helper
checks the final project config, the environment (an override is a violation), immutable files, and geometry (ORFS ``6_report.json``:
standard-cell area = all standard-cell instances except fill, core and die area from the geometry, square micrometres).
A missing mandatory measurement makes the episode UNVERIFIED, never accepted.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

AREA_KEYS = {"stdcell": "finish__design__instance__area__stdcell", "core": "finish__design__core__area",
             "die": "finish__design__die__area"}
_EXPORT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*([?:]?=)\s*(.*?)\s*$")


def effective_config(text: str) -> dict:
    """Read only flat literal assignments; never claim unresolved Make syntax is effective.

    Full ORFS overrides/derived settings still require execution-bound evidence. Reject
    includes, expansion, continuations and directives instead of silently ignoring them.
    """
    out = {}
    for ln in (text or "").splitlines():
        ln = ln.split('#', 1)[0].strip()
        if not ln:
            continue
        m = _EXPORT.match(ln)
        if not m or '$' in ln or '\\' in ln:
            raise ValueError('unresolved Make syntax: ' + ln)
        key, op, value = m.groups()
        if op != '?=' or key not in out:
            out[key] = value
    return out


def _num(v):
    try:
        if isinstance(v, bool):
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def check_config(manifest: dict, final_cfg: dict, env: dict | None = None, project: Path | None = None) -> dict:
    """Violations of the allowlist, ranges, step limits, immutable knobs/files and environment overrides."""
    init = manifest.get("initial_config") or {}
    allow = manifest.get("allowlist") or {}
    imm = manifest.get("immutable") or {}
    violations = []
    for k in sorted(set(init) | set(final_cfg)):
        old, new = init.get(k), final_cfg.get(k)
        if str(old) == str(new):
            continue
        if k in (imm.get("knobs") or []):
            violations.append({"kind": "immutable_knob_changed", "knob": k, "old": old, "new": new})
            continue
        rule = allow.get(k)
        if rule is None:
            violations.append({"kind": "not_allowlisted", "knob": k, "old": old, "new": new})
            continue
        if rule.get("type") == "enum":
            if new is not None and new not in rule.get("values", []):
                violations.append({"kind": "value_not_allowed", "knob": k, "new": new})
            continue
        if rule.get('type') == 'rectangle':
            if new is not None:
                parts = str(new).split()
                nums = [_num(v) for v in parts]
                if (len(nums) != 4 or any(v is None or v < 0 for v in nums)
                    or nums[2] <= nums[0] or nums[3] <= nums[1]):
                    violations.append({'kind':'invalid_rectangle','knob':k,'new':new})
            continue
        x = _num(new)
        if new is not None and x is None:
            violations.append({"kind": "non_numeric", "knob": k, "new": new})
            continue
        if x is not None and not (rule.get("min", float("-inf")) <= x <= rule.get("max", float("inf"))):
            violations.append({"kind": "out_of_range", "knob": k, "new": new, "range": [rule.get("min"), rule.get("max")]})
        o = _num(old)
        if x is not None and o is not None and rule.get("max_step") is not None and abs(x - o) > rule["max_step"]:
            violations.append({"kind": "step_too_large", "knob": k, "old": old, "new": new, "max_step": rule["max_step"]})
    for k in sorted(set(allow) | set(imm.get("knobs") or [])):
        if env and k in env:                              # an equivalent override outside config.mk is forbidden
            violations.append({"kind": "environment_override", "knob": k, "value": env[k]})
    if manifest.get('floorplan_mode_policy')=='explicit_util_or_fixed_rectangles':
        util=bool(final_cfg.get('CORE_UTILIZATION'))
        die,core=bool(final_cfg.get('DIE_AREA')),bool(final_cfg.get('CORE_AREA'))
        if not ((util and not die and not core) or (not util and die and core)):
            violations.append({'kind':'ambiguous_floorplan_mode','CORE_UTILIZATION':final_cfg.get('CORE_UTILIZATION'),
                               'DIE_AREA':final_cfg.get('DIE_AREA'),'CORE_AREA':final_cfg.get('CORE_AREA')})
        elif die and core:
            try:
                d=list(map(float,final_cfg['DIE_AREA'].split()));c=list(map(float,final_cfg['CORE_AREA'].split()))
                if len(d)==len(c)==4 and not (d[0]<=c[0]<c[2]<=d[2] and d[1]<=c[1]<c[3]<=d[3]):
                    violations.append({'kind':'core_outside_die'})
            except ValueError:
                pass  # the rectangle validator above already records the invalid value
    for rel, digest in sorted((imm.get("files") or {}).items()):
        p = Path(project or ".") / rel
        got = ("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else None
        if got != digest:
            violations.append({"kind": "immutable_file_changed", "file": rel, "expected": digest, "found": got})
    return {"ok": not violations, "violations": violations}


def _finished(run: Path) -> bool:
    """failure-patterns #61: an early-aborting rerun carried the previous flow's 6_report.json
    into its run dir, so finish areas count only when this run's own stage log shows finish."""
    try:
        rows = [json.loads(l) for l in (run / "stage_log.jsonl").read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return False
    return any(r.get("stage") == "finish" and str(r.get("status")) in ("0", "pass") for r in rows)


def area_metrics(run_dir: Path | None) -> dict:
    """{stdcell, core, die} in um^2 from the run's finish report; a metric that is absent is None (UNKNOWN)."""
    rep = Path(run_dir) / "logs" / "6_report.json" if run_dir else None
    try:
        data = json.loads(rep.read_text()) if rep and rep.is_file() and _finished(Path(run_dir)) else {}
    except ValueError:
        data = {}
    return {m: (_num(data.get(k)) if data.get(k) is not None else None) for m, k in AREA_KEYS.items()}


def check_area(manifest: dict, metrics: dict) -> dict:
    """Per metric: absolute area, relative change against its own frozen reference, and the cap."""
    out, violations, unverified = {}, [], []
    if manifest.get("area_reference_policy"):
        from t2_nb_firstlook import resolve_area_reference
        manifest = resolve_area_reference(manifest)
    specs = manifest.get('area') or {}
    for m in AREA_KEYS:
        spec = specs.get(m)
        if not isinstance(spec, dict):
            unverified.append(m + ':missing_definition')
            continue
        ref, cap, a = _num(spec.get("ref")), spec.get("max_rel_increase"), _num(metrics.get(m))
        valid_ref, valid_area = ref is not None and ref > 0, a is not None and a > 0
        rel = (a / ref - 1.0) if valid_ref and valid_area else None
        out[m] = {"final": a, "ref": ref, "ref_stage": spec.get("ref_stage"), "relative_change": rel, "cap": cap}
        if 'max_rel_increase' not in spec or (m in ('core', 'die') and cap is None):
            unverified.append(m + ':missing_cap')
            continue
        if cap is None:
            continue                                          # declared unconstrained: measured, not limited
        cap = _num(cap)
        if cap is None or cap < 0:
            unverified.append(m + ':invalid_cap')
        elif not valid_area or not valid_ref:
            unverified.append(m)                              # mandatory evidence missing: never accepted
        elif rel > cap + 1e-12:
            violations.append({"kind": "area_cap_exceeded", "metric": m, "relative_change": rel, "cap": cap})
    return {"ok": not violations and not unverified, "violations": violations, "unverified": unverified,
            "metrics": out}


def execution_binding(manifest: dict, project: Path, run_dir: Path | None, env: dict | None = None) -> bool:
    """Execution provenance only; does NOT certify completion, area, or signoff."""
    if not manifest.get('require_execution_binding'):
        return True
    try:
        binding=json.loads((Path(run_dir)/'effective-config.json').read_text()) if run_dir else {}
        actual=hashlib.sha256((Path(project)/'constraints/config.mk').read_bytes()).hexdigest()
        mbytes=Path((env or {}).get('R2G_TASK_MANIFEST','')).read_bytes()
        return (binding.get('status')=='PASS' and binding.get('run_dir')==str(run_dir)
                and binding.get('project_config_sha256')==actual and json.loads(mbytes)==manifest
                and binding.get('manifest_sha256')==hashlib.sha256(mbytes).hexdigest())
    except (OSError,ValueError,TypeError,AttributeError):
        return False


def envelope(manifest: dict, project: Path, run_dir: Path | None, env: dict | None = None) -> dict:
    """The full section 3.3 check of an episode's final state."""
    try:
        cfg = effective_config((Path(project) / "constraints" / "config.mk").read_text(errors="strict"))
    except (OSError, ValueError) as exc:
        return {'status': 'UNVERIFIED', 'reason': 'effective_config_unresolved', 'detail': str(exc),
                'task': manifest.get('task')}
    c = check_config(manifest, cfg, env=env, project=project)
    if not execution_binding(manifest,project,run_dir,env):
        return {'status':'UNVERIFIED','reason':'execution_config_binding_missing_or_stale','task':manifest.get('task')}
    a = check_area(manifest, area_metrics(run_dir))
    status = "PASS" if (c["ok"] and a["ok"]) else ("UNVERIFIED" if (c["ok"] and not a["violations"]) else "FAIL")
    return {"status": status, "config": c, "area": a, "task": manifest.get("task")}


def recorded_violations(manifest: dict, fix_rows: list[dict]) -> list[dict]:
    """Intermediate violations from the episode's recorded edits (kept even when later rolled back)."""
    out = []
    for r in fix_rows:
        delta = r.get("config_delta") or {}
        if isinstance(delta, str):
            try:
                delta = json.loads(delta)
            except ValueError:
                delta = {}
        flat = {k: (v.get("after") if isinstance(v, dict) else v) for k, v in delta.items()}
        cfg = {**(manifest.get("initial_config") or {})}
        for k, v in flat.items():
            if v is None: cfg.pop(k, None)
            else: cfg[k] = v
        res = check_config({**manifest, "immutable": {"knobs": (manifest.get("immutable") or {}).get("knobs", [])}}, cfg)
        for v in res["violations"]:
            if v.get("knob") in flat:
                out.append({**v, "iter": r.get("iter"), "strategy": r.get("strategy"),
                            "rolled_back": r.get("rolled_back")})
    return out
