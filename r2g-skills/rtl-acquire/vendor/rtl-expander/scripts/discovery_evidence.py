#!/usr/bin/env python3
"""Deterministic multi-evidence scoring for RTL repository discovery."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any


EVIDENCE_SCHEMA = "rtl_discovery_evidence_v1_1"
PRECISION_EVIDENCE_SCHEMA = "rtl_discovery_evidence_v2"
RTL_PRESENCE_SCHEMA = "rtl_presence_score_v1"
HDL_LANGUAGES = {"verilog", "systemverilog", "vhdl"}
HDL_RE = re.compile(r"(?:^|[-_\s])(rtl|hdl|verilog|systemverilog|vhdl|fpga|asic)(?:$|[-_\s])", re.I)
DOMAIN_RE = re.compile(
    r"(?:risc-?v|processor|cpu|soc|noc|crossbar|interconnect|axi|wishbone|memory|ddr|cache|dma|"
    r"ethernet|pcie|crypto|accelerator|dsp|matrix|video|image|signal|controller|core)", re.I,
)
STRONG_NON_RTL_LANGUAGES = {
    "java", "javascript", "typescript", "html", "css", "kotlin", "swift", "dart", "objective-c",
}
STRONG_NON_RTL_RE = re.compile(
    r"(?:node\.?js|npm\s+package|web\s*(?:app|application|frontend|backend)|font|typograph|"
    r"mobile\s+app|ide\s+(?:plugin|extension)|kernel|driver|dkms|compiler|toolchain|simulator|"
    r"emulator|language.server)", re.I,
)
SOFT_NON_RTL_RE = re.compile(
    r"(?:userspace|software\s+sdk|parser|plugin|docker|dashboard|monitor|firmware|android|linux|documentation)", re.I,
)
GRAPH_STRATEGIES = {"upstream", "fork", "submodule", "dependency", "organization", "organization_sibling", "readme_reference"}
VERIFIED_GRAPH_ANCHORS = {
    "VERIFIED_RTL_DEPENDENCY", "VERIFIED_RTL_SUBMODULE",
    "VERIFIED_RTL_PROJECT_REFERENCE", "VERIFIED_RTL_GRAPH_NEIGHBOR",
}


def precision_policy_round(factory_round_id: str) -> bool:
    """Enable the post-Batch-3 policy without changing a live earlier batch."""
    match = re.search(r"(?:^|_)batch(?P<sequence>\d+)$", factory_round_id)
    return bool(match and int(match.group("sequence")) >= 4)


def normalized_anchor(anchor: str) -> str:
    return "RTL_QUERY_ORIGIN" if anchor == "RTL_QUERY" else anchor


def effective_provider_query(provider: str, query_text: str) -> str:
    """Return only query semantics the provider actually executes."""
    value = query_text.strip()
    # These provider APIs currently search one opaque term; their adapters send
    # only the first token.  Scoring the discarded tokens would manufacture RTL
    # evidence for unrelated results (for example `noc systemverilog` matching
    # a memorial site on `noc` alone).
    if provider.lower() in {"gitlab", "codeberg"}:
        return value.split()[0] if value else ""
    return value


def _text(candidate: dict[str, Any]) -> str:
    return " ".join(str(candidate.get(key) or "") for key in (
        "url", "description", "ecosystem", "primary_language", "core_path", "evidence",
    ))


def score_discovery_evidence(
    candidate: dict[str, Any], *, query_text: str = "", strategy: str = "",
    graph_source_trusted: bool = False, existing: dict[str, Any] | None = None,
    precision_policy: dict[str, Any] | None = None,
    graph_evidence_kind: str | None = None,
) -> dict[str, Any]:
    """Aggregate independent evidence without treating missing metadata as negative."""
    text = _text(candidate)
    language = str(candidate.get("primary_language") or "").lower()
    direct_language = language in HDL_LANGUAGES
    direct_file = bool(re.search(r"\.(?:svh?|v|vhd|vhdl)(?:$|[?#\s])", text, re.I))
    language_query = bool(re.search(r"\blanguage:(?:verilog|systemverilog|vhdl)\b", query_text, re.I))
    manifest = (
        str(candidate.get("ecosystem") or "").lower() in {"fusesoc", "edalize", "ip-xact"}
        or bool(re.search(r"(?:\.core|bender\.ya?ml|fusesoc\.conf|\.xpr|\.qpf|\.qsf)(?:$|[?#\s])", text, re.I))
    )
    graph = strategy in GRAPH_STRATEGIES
    strong_graph = strategy in {"upstream", "submodule", "dependency", "readme_reference"}
    metadata_positive = bool(HDL_RE.search(text))
    query_hdl = bool(HDL_RE.search(query_text))
    query_domain = bool(DOMAIN_RE.search(query_text))
    strong_rtl_query = language_query or bool(
        query_hdl and (
            query_domain
            or re.search(r"(?:synthesizable|register.transfer|hardware.design|logic.design|full\s+rtl)", query_text, re.I)
        )
    )
    strong_non_rtl = language in STRONG_NON_RTL_LANGUAGES or bool(STRONG_NON_RTL_RE.search(text))
    soft_non_rtl = bool(SOFT_NON_RTL_RE.search(text))
    repository_bytes = int(candidate.get("size_bytes") or 0)
    scale_evidence = []
    if repository_bytes >= 20 * 1024 * 1024:
        scale_evidence.append("PROVIDER_REPOSITORY_SIZE")
    if manifest:
        scale_evidence.append("HDL_MANIFEST_OR_CORE")
    if strong_graph:
        scale_evidence.append("IP_DEPENDENCY_OR_REFERENCE_EDGE")

    local_anchors: list[str] = []
    if direct_language:
        local_anchors.append("DIRECT_HDL_LANGUAGE")
    if direct_file:
        local_anchors.append("DIRECT_HDL_FILE")
    if manifest:
        local_anchors.append("HDL_MANIFEST")
    policy_active = bool(precision_policy and precision_policy.get("status") == "ACTIVE")
    if strong_rtl_query:
        local_anchors.append("RTL_QUERY_ORIGIN" if policy_active else "RTL_QUERY")
    if policy_active and graph_source_trusted and graph_evidence_kind in VERIFIED_GRAPH_ANCHORS:
        local_anchors.append(graph_evidence_kind)
    prior = existing if isinstance(existing, dict) and existing.get("schema") in {
        EVIDENCE_SCHEMA, PRECISION_EVIDENCE_SCHEMA, "rtl_discovery_evidence_v1",
    } else {}
    prior_anchors = [normalized_anchor(str(value)) if policy_active else str(value) for value in prior.get("rtl_anchors", [])]
    anchors = list(dict.fromkeys([*prior_anchors, *local_anchors]))
    direct_anchor = any(anchor in {"DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "HDL_MANIFEST"} for anchor in anchors)
    verified_graph_anchor = any(anchor in VERIFIED_GRAPH_ANCHORS for anchor in anchors)
    content_anchors = [anchor for anchor in anchors if anchor in {
        "DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "HDL_MANIFEST", *VERIFIED_GRAPH_ANCHORS,
    }]

    components = {
        "direct_language": 0.95 if direct_language else 0.0,
        "direct_file": 0.95 if direct_file else 0.0,
        "language_qualified_query": 0.88 if language_query else 0.0,
        "manifest": 0.90 if manifest else 0.0,
        # Graph proximity changes scheduling priority, never RTL likelihood.
        "graph": 0.45 if graph else 0.0,
        # Ordinary provider keyword search is supporting evidence, not direct
        # file/language proof (notably GitLab only searches the first token).
        "query": 0.72 if strong_rtl_query else 0.48 if query_hdl else 0.42 if query_domain else 0.0,
        "metadata": 0.48 if metadata_positive else 0.0,
        "strong_non_rtl": -0.90 if strong_non_rtl else 0.0,
        "soft_non_rtl": -0.15 if soft_non_rtl else 0.0,
    }
    prior_components = prior.get("components") if isinstance(prior.get("components"), dict) else {}
    for key, value in prior_components.items():
        if key not in {"negative", "strong_non_rtl", "soft_non_rtl"}:
            components[key] = max(float(components.get(key, 0.0)), float(value or 0.0))
    components["strong_non_rtl"] = min(float(components["strong_non_rtl"]), float(prior_components.get("strong_non_rtl", 0.0)))
    components["soft_non_rtl"] = min(float(components["soft_non_rtl"]), float(prior_components.get("soft_non_rtl", 0.0)))

    positive = max(value for key, value in components.items() if key not in {"strong_non_rtl", "soft_non_rtl", "negative"})
    if policy_active and verified_graph_anchor and not direct_anchor:
        score = 0.78
    elif positive == 0:
        score = 0.40  # unknown/absent evidence is neutral, not a failure
    elif strong_non_rtl and not direct_anchor:
        score = 0.10
    else:
        score = max(0.05, min(0.99, positive + components["soft_non_rtl"] + (components["strong_non_rtl"] * 0.2 if direct_anchor else 0.0)))
    if policy_active and not content_anchors:
        # Query/organization provenance says where a candidate came from; it is
        # not evidence that an archive contains RTL.
        score = min(score, 0.49)
    elif not anchors:
        score = min(score, 0.49)

    if policy_active and content_anchors and len(anchors) > 1:
        admission_anchor = "MULTI_EVIDENCE"
    elif policy_active and content_anchors:
        admission_anchor = "VERIFIED_RTL_GRAPH_NEIGHBOR" if content_anchors[0] in VERIFIED_GRAPH_ANCHORS else content_anchors[0]
    elif policy_active and "RTL_QUERY_ORIGIN" in anchors:
        admission_anchor = "RTL_QUERY_ORIGIN"
    elif len(anchors) > 1:
        admission_anchor = "MULTI_EVIDENCE"
    elif anchors:
        admission_anchor = anchors[0]
    elif graph:
        admission_anchor = "ORGANIZATION_ONLY" if strategy in {"organization", "organization_sibling"} else "GRAPH_ONLY"
    elif query_hdl or query_domain:
        admission_anchor = "QUERY_ONLY"
    elif metadata_positive:
        admission_anchor = "METADATA_ONLY"
    else:
        admission_anchor = "UNANCHORED"
    priority_bonus = (
        3.5 if anchors else 3.0 if graph_source_trusted and graph else 2.5 if strong_graph else
        1.5 if graph else 1.25 if query_hdl or query_domain else 0.0
    )
    # Scale evidence changes scheduling order only.  It never raises RTL
    # likelihood and therefore cannot promote an unanchored software repository
    # into the precision lane.
    priority_bonus += min(2.0, 0.75 * len(scale_evidence))

    reasons: list[str] = []
    for enabled, reason in (
        (direct_language, "DIRECT_LANGUAGE"), (direct_file, "DIRECT_FILE"),
        (language_query, "LANGUAGE_COVERAGE_QUERY"), (manifest, "HDL_MANIFEST"),
        (graph_source_trusted, "TRUSTED_GRAPH_NEIGHBOR"), (graph and not graph_source_trusted, "GRAPH_NEIGHBOR"),
        (query_hdl, "HDL_QUERY_EVIDENCE"), (query_domain and not query_hdl, "DOMAIN_QUERY_EVIDENCE"),
        (metadata_positive, "METADATA_RTL_EVIDENCE"), (strong_non_rtl, "STRONG_NON_RTL_EVIDENCE"),
        (soft_non_rtl, "SOFT_NON_RTL_EVIDENCE"),
    ):
        if enabled and reason not in reasons:
            reasons.append(reason)
    for reason in prior.get("eligible_reasons", []):
        if reason not in reasons:
            reasons.append(reason)
    cell = (precision_policy or {}).get("admission_anchor_cells", {}).get(admission_anchor, {})
    if not cell and admission_anchor == "RTL_QUERY_ORIGIN":
        cell = (precision_policy or {}).get("admission_anchor_cells", {}).get("RTL_QUERY", {})
    default_lane = (
        "PRODUCTION" if admission_anchor in {
            "DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "HDL_MANIFEST",
            "MULTI_EVIDENCE", "VERIFIED_RTL_GRAPH_NEIGHBOR",
        } else "EXPLORATION"
    )
    admission_tier = str(cell.get("tier") or default_lane) if policy_active else "LEGACY"
    exploration = (
        0.30 <= score < 0.50 and not strong_non_rtl
        and bool(query_domain or query_hdl or graph or prior.get("exploration_eligible"))
        and admission_tier != "DORMANT"
    )
    expected_family_yield = float(cell.get("design_families_per_revision") or (0.25 if default_lane == "PRODUCTION" else 0.05))
    expected_cost = max(0.1, 1.0 + repository_bytes / float(512 * 1024 * 1024))
    design_value_score = min(1.0, expected_family_yield / 0.35) + min(0.5, 0.1 * len(scale_evidence))
    scheduler_utility = score * expected_family_yield * design_value_score / expected_cost
    return {
        "schema": PRECISION_EVIDENCE_SCHEMA if policy_active else EVIDENCE_SCHEMA,
        "policy_version": "discovery_precision_recalibration_v1" if policy_active else "v4.4.1",
        "score": round(score, 6),
        "rtl_presence_score": {"schema": RTL_PRESENCE_SCHEMA, "probability": round(score, 6)},
        "design_value_score": round(design_value_score, 6),
        "expected_formal_family_yield": round(expected_family_yield, 6),
        "expected_acquisition_processing_cost": round(expected_cost, 6),
        "scheduler_utility": round(scheduler_utility, 9),
        "admission_tier": admission_tier,
        "tier": "A" if score >= 0.8 else "B" if score >= 0.5 else "C" if exploration else "UNKNOWN",
        "components": components,
        "eligible_reasons": reasons,
        "exploration_eligible": exploration,
        "rtl_anchors": anchors,
        "rtl_content_evidence": content_anchors,
        "origin_evidence": [anchor for anchor in anchors if anchor not in content_anchors],
        "admission_anchor": admission_anchor,
        "priority_bonus": priority_bonus,
        "strong_non_rtl_evidence": strong_non_rtl,
        "soft_non_rtl_evidence": soft_non_rtl,
        "negative_evidence": strong_non_rtl or soft_non_rtl,
        "scale_evidence": scale_evidence,
        "provider_repository_bytes": repository_bytes,
    }


LANGUAGE_WINDOW_RE = re.compile(
    r"^(?P<prefix>.*\blanguage:(?:Verilog|SystemVerilog|VHDL)\b.*?)\s+created:"
    r"(?P<start>\d{4}-\d{2}-\d{2})\.\.(?P<end>\d{4}-\d{2}-\d{2})$", re.I,
)


def split_language_date_query(query: str, total_count: int, cap: int = 1000) -> list[str]:
    """Split capped GitHub language searches into lossless non-overlapping date windows."""
    match = LANGUAGE_WINDOW_RE.match(query.strip())
    if total_count <= cap or not match:
        return []
    start = dt.date.fromisoformat(match.group("start"))
    end = dt.date.fromisoformat(match.group("end"))
    if start >= end:
        return []
    midpoint = start + (end - start) // 2
    right = midpoint + dt.timedelta(days=1)
    prefix = match.group("prefix").strip()
    return [
        f"{prefix} created:{start.isoformat()}..{midpoint.isoformat()}",
        f"{prefix} created:{right.isoformat()}..{end.isoformat()}",
    ]
