"""Asset Memory: registry, gap receipts, validation, and lifecycle authority."""
from .gap_detector import detect_capability_gap, detect_capability_gaps
from .authority import (
    promote_asset, record_asset_authority, verify_asset_authority,
)
from .lifecycle import (
    ASSET_PROMOTION_GATES, evaluate_asset_authority,
    evaluate_asset_promotion_gates,
)
from .receipts import (
    AssetAuthorityReceipt, AssetPromotionReceipt, AssetReceipt,
    AssetValidationReceipt, CapabilityGapReceipt, RuntimeBindingReceipt,
)
from .registry import (
    asset_content_digest, get_asset, get_asset_status, register_asset,
    set_asset_status,
)
from .schema import ASSET_STATUSES, ASSET_TYPES
from .synthesis import (
    AssetProposal, bind_asset_to_repair_context, bind_rtl_asset_to_project,
    build_rtl_asset_proposal,
    register_asset_proposal, synthesize_asset, synthesize_rtl_asset,
)
from .validation import (
    validate_asset_schema, validate_rtl_asset_project, validate_rtl_rewrite_asset,
)

__all__ = [
    "ASSET_PROMOTION_GATES", "ASSET_STATUSES", "ASSET_TYPES",
    "AssetAuthorityReceipt",
    "AssetProposal",
    "AssetPromotionReceipt", "AssetReceipt", "AssetValidationReceipt",
    "CapabilityGapReceipt", "RuntimeBindingReceipt", "detect_capability_gap",
    "detect_capability_gaps",
    "evaluate_asset_authority", "evaluate_asset_promotion_gates", "get_asset",
    "get_asset_status",
    "asset_content_digest",
    "register_asset", "set_asset_status",
    "promote_asset", "record_asset_authority", "verify_asset_authority",
    "bind_asset_to_repair_context", "bind_rtl_asset_to_project",
    "build_rtl_asset_proposal",
    "register_asset_proposal",
    "synthesize_asset", "synthesize_rtl_asset", "validate_asset_schema",
    "validate_rtl_rewrite_asset",
    "validate_rtl_asset_project",
]
