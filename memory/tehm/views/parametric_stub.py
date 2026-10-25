"""Parametric view stub (design doc 22.5).

Phase 1 keeps ONLY the interface and provenance:
    ``parametric_view_status = NOT_IMPLEMENTED``

We do NOT fabricate steering vectors / adapters / learned rankers just to fill a
view — a fake parametric view would be a fake contribution (design doc 22.5).
"""
from __future__ import annotations

PARAMETRIC_VIEW_STATUS = "NOT_IMPLEMENTED"
PARAMETRIC_EXTRACTOR_VERSION = "parametric-stub-v0.1"


def build_parametric_view(*_args, **_kwargs):
    raise NotImplementedError(
        "parametric view is NOT_IMPLEMENTED in Phase 1 (design doc 22.5). "
        "Do not fill this view with stub data."
    )
