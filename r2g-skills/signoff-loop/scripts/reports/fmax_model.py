#!/usr/bin/env python3
"""Pure model + helpers for the Fmax search. No I/O, no subprocess — every
function here is unit-testable. The orchestrator (fmax_search.py) supplies real
probe callables to ``search_loop``.

Slack-deterioration model (per design spec 2026-06-04, §5.1):
  d_fp_pl  = floorplan_ws - place_ws    (placement erosion — dominant)
  d_pl_fin = place_ws    - finish_ws    (routing erosion — tiny, often negative)
  d_fp_fin = d_fp_pl + d_pl_fin
Estimator = p90, applied as max(ns_floor, pct*period), clamped >= 0.
"""
from __future__ import annotations
import re

UNCONSTRAINED = 1e30
N_MIN_FAMILY = 8
N_MIN_PLATFORM = 20

# Cold-start (ns_floor, pct-of-period) defaults from the corpus (spec §5.1 table).
DEFAULT_D_FP_PL = (0.45, 0.045)
DEFAULT_D_PL_FIN = (0.10, 0.010)

_CLK_RE = re.compile(r"(set\s+clk_period\s+)([0-9.]+)")


def _term(default: tuple[float, float], period: float,
          learned: tuple[float, float] | None) -> float:
    ns, pct = learned if learned is not None else default
    return max(0.0, ns, pct * period)


def d_fp_pl(period: float, model: dict | None = None) -> float:
    return _term(DEFAULT_D_FP_PL, period, (model or {}).get("d_fp_pl"))


def d_pl_fin(period: float, model: dict | None = None) -> float:
    return _term(DEFAULT_D_PL_FIN, period, (model or {}).get("d_pl_fin"))


def d_fp_fin(period: float, model: dict | None = None) -> float:
    return d_fp_pl(period, model) + d_pl_fin(period, model)


def classify_probe(place_ws: float | None, place_tns: float | None,
                   period: float, model: dict | None = None,
                   completed: bool = True) -> str:
    """'pass' | 'fail' | 'inconclusive' at the placement reference stage."""
    if not completed:
        return "inconclusive"
    if place_ws is None or place_ws > UNCONSTRAINED:
        return "inconclusive"
    if place_tns is None or place_tns < 0:
        return "fail"
    return "pass" if place_ws >= d_pl_fin(period, model) else "fail"


def variant_name(base: str, period: float) -> str:
    """Unique FLOW_VARIANT per period: <base>_fmax_p<NNNN> (NNNN = period*10)."""
    return f"{base}_fmax_p{int(round(period * 10)):04d}"


def rewrite_clk_period(sdc_text: str, period: float) -> str:
    new, n = _CLK_RE.subn(rf"\g<1>{period:g}", sdc_text, count=1)
    if n == 0:
        raise ValueError("no 'set clk_period' line found in SDC")
    return new


def _as_model(sd: dict) -> dict:
    return {"d_fp_pl": (sd["d_fp_pl"]["ns_p90"], sd["d_fp_pl"]["pct_p90"]),
            "d_pl_fin": (sd["d_pl_fin"]["ns_p90"], sd["d_pl_fin"]["pct_p90"])}


def select_model(entry: dict | None,
                 n_min_family: int = N_MIN_FAMILY,
                 platform_entry: dict | None = None,
                 n_min_platform: int = N_MIN_PLATFORM) -> tuple[dict | None, str]:
    """Pick the deterioration model + provenance: the family model when it has
    >= n_min_family samples, else the platform-pooled model when that has
    >= n_min_platform, else (None, 'default-static…') -> cold-start defaults."""
    sd = (entry or {}).get("slack_deterioration")
    n = (sd or {}).get("n", 0)
    if sd and n >= n_min_family:
        return _as_model(sd), f"learned(n={n},q=p90)"
    psd = (platform_entry or {}).get("slack_deterioration")
    pn = (psd or {}).get("n", 0)
    if psd and pn >= n_min_platform:
        return _as_model(psd), f"learned-platform(n={pn},q=p90; family n={n}<{n_min_family})"
    if not sd:
        return None, "default-static"
    return None, f"default-static(family n={n}<{n_min_family})"


def estimate_fmax_fp(t_ref: float, floorplan_ws: float) -> float:
    """Floorplan-stage Fmax point estimate = period - worst slack."""
    return t_ref - floorplan_ws


def search_loop(seed_period, floorplan_probe, place_probe, model=None, *,
                floor=0.05, max_iter=3, tol=None):
    """Tier-1 floorplan early-look + Tier-2 fixed-point root-find.

    floorplan_probe(period) -> floorplan setup_ws (float) | None
    place_probe(period)     -> {'place_ws', 'place_tns', 'status'}
    Returns a dict: status in {'ok','inconclusive','error'} plus the trace 'log'.
    """
    log: list[dict] = []
    t_ref = float(seed_period)

    fp_ws = floorplan_probe(t_ref)
    log.append({"stage": "floorplan", "period": t_ref, "ws": fp_ws})
    if fp_ws is not None and fp_ws > UNCONSTRAINED:
        # No constraining timing path (e.g. combinational logic): there is no Fmax to search.
        # An HONEST distinct status -- NOT 'error' (which read as a tool failure and silently
        # gave 26 CLEAN designs no Fmax at all; 2026-06-29).
        return {"status": "unconstrained", "reason": "floorplan_unconstrained", "log": log}
    if fp_ws is None:
        # The pre-place floorplan stage yielded NO slack (common for sequential designs whose
        # timing is only meaningful post-placement) -> do NOT abort: skip the floorplan
        # early-look and seed the place root-find from the seed period, which reads the more
        # reliable POST-PLACE slack (recovers Fmax for r8051_core, RISC-V cores, ...; 2026-06-29).
        t_ref = max(float(seed_period), floor)
    else:
        fmax_fp = estimate_fmax_fp(t_ref, fp_ws)
        if abs(fmax_fp - t_ref) > 0.5 * t_ref:
            # Bad seed: jump to the corrected center and re-probe floorplan once.
            t_ref = max(fmax_fp + d_fp_fin(fmax_fp, model), floor)
            fp_ws = floorplan_probe(t_ref)
            log.append({"stage": "floorplan_restart", "period": t_ref, "ws": fp_ws})
            if fp_ws is not None and fp_ws > UNCONSTRAINED:
                return {"status": "unconstrained", "reason": "floorplan_unconstrained", "log": log}
            fmax_fp = estimate_fmax_fp(t_ref, fp_ws) if fp_ws is not None else None
        if fmax_fp is None:                       # restart also null -> fall back to seed period
            t_ref = max(float(seed_period), floor)
        else:
            # Bracket center = predicted-signoff closing period (pre-absorb erosion).
            t_ref = max(fmax_fp + d_fp_fin(fmax_fp, model), floor)
    if tol is None:
        tol = max(0.1, 0.02 * t_ref)

    last_pass = None
    for _ in range(max_iter):
        r = place_probe(t_ref)
        log.append({"stage": "place", "period": t_ref,
                    "ws": r.get("place_ws"), "status": r.get("status")})
        if r.get("status") == "inconclusive":
            if last_pass is not None:
                # A faster probe may fail for tool/runtime reasons after a slower
                # placement point already passed. Preserve that measured feasible
                # point as a conservative winner instead of discarding the whole
                # search and silently falling back to the default SDC.
                place_proxy_period = max(last_pass - d_pl_fin(last_pass, model), floor)
                return {
                    "status": "ok",
                    "reason": "recovered_last_pass_after_inconclusive",
                    "t_star": last_pass,
                    "last_pass": last_pass,
                    "fmax_predicted_signoff": 1.0 / last_pass,
                    "t_place_proxy": place_proxy_period,
                    "fmax_place_proxy": 1.0 / place_proxy_period,
                    "log": log,
                }
            # Carry a reason like every other non-ok exit (2026-07-05: 144 of 144
            # inconclusive fmax reports had NO queryable cause — the same
            # observability gap the judge-v2 reason codes fixed for A/B trials).
            return {"status": "inconclusive", "reason": "place_probe_inconclusive",
                    "period": t_ref, "log": log}
        place_ws = r.get("place_ws")
        if place_ws is None:
            # place yielded no slack but did not classify inconclusive -> cannot root-find; honest
            return {"status": "inconclusive", "reason": "place_no_slack", "period": t_ref, "log": log}
        if place_ws > UNCONSTRAINED:
            return {"status": "unconstrained", "reason": "place_unconstrained", "period": t_ref, "log": log}
        if r.get("status") == "pass":
            last_pass = t_ref
        t_next = (t_ref - place_ws) + d_pl_fin(t_ref, model)
        if abs(t_next - t_ref) < tol:
            t_ref = max(t_next, floor)
            break
        t_ref = max(t_next, floor)

    place_proxy_period = max(t_ref - d_pl_fin(t_ref, model), floor)
    return {
        "status": "ok",
        "t_star": t_ref,
        "last_pass": last_pass,
        "fmax_predicted_signoff": 1.0 / t_ref,
        "t_place_proxy": place_proxy_period,
        "fmax_place_proxy": 1.0 / place_proxy_period,
        "log": log,
    }


# --- Post-search period relaxation chain (2026-09-27) ------------------------
# The search winner is a placement proxy; when the confirming full flow misses
# timing, the repair path (check_timing minor bump, diagnose period_relax) loosens
# the SDC period. Without a record the manifest saw stamped != winner and called a
# genuinely closed design unqualified. Each loosening is appended to
# reports/fmax_search.json["relaxations"] as {from, to, source}; the manifest
# accepts the stamped period only when that chain runs UNBROKEN from the winner,
# every step strictly looser. Tightening, gaps, or an unrecorded hand edit still
# disqualify — the chain is evidence, not a waiver.
PERIOD_RTOL = 1e-3   # the SDC stamp rounds to ~6 significant digits


def periods_match(a: float, b: float, rtol: float = PERIOD_RTOL) -> bool:
    return abs(float(a) - float(b)) <= rtol * max(abs(float(b)), 1e-9)


def add_relaxation(report: dict, old: float, new: float, source: str,
                   ts: str | None = None) -> dict:
    """Return a copy of an fmax_search report with one relaxation step appended."""
    out = dict(report)
    step = {"from": float(old), "to": float(new), "source": source}
    if ts:
        step["ts"] = ts
    out["relaxations"] = list(report.get("relaxations") or []) + [step]
    return out


def resolve_confirmed_period(report: dict | None, stamped: float | None,
                             rtol: float = PERIOD_RTOL) -> dict:
    """Decide which period the stamped SDC legitimately represents.

    Returns {period_source: 'search_winner'|'relaxed_chain'|None,
             confirmed_period, relax_ratio, relaxations, chain_error}."""
    winner = ((report or {}).get("winner") or {}).get("period")
    steps = list((report or {}).get("relaxations") or [])
    res = {"period_source": None, "confirmed_period": None, "relax_ratio": None,
           "relaxations": steps, "chain_error": None}
    if winner is None or stamped is None:
        res["chain_error"] = "no search winner or no stamped period"
        return res
    if periods_match(stamped, winner, rtol):
        res.update(period_source="search_winner", confirmed_period=float(stamped),
                   relax_ratio=1.0)
        return res
    if not steps:
        res["chain_error"] = "no recorded relaxation from the search winner"
        return res
    prev = float(winner)
    for i, s in enumerate(steps):
        try:
            frm, to = float(s["from"]), float(s["to"])
        except (KeyError, TypeError, ValueError):
            res["chain_error"] = f"relaxation step {i} is malformed"
            return res
        if not periods_match(frm, prev, rtol):
            res["chain_error"] = f"relaxation step {i} starts at {frm}, chain is at {prev}"
            return res
        if not (to > frm and not periods_match(to, frm, rtol)):
            res["chain_error"] = f"relaxation step {i} is not a loosening ({frm} -> {to})"
            return res
        prev = to
    if not periods_match(stamped, prev, rtol):
        res["chain_error"] = f"relaxation chain ends at {prev}, SDC is stamped {stamped}"
        return res
    res.update(period_source="relaxed_chain", confirmed_period=float(stamped),
               relax_ratio=float(stamped) / float(winner))
    return res
