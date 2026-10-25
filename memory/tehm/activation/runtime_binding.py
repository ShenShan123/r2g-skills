"""Runtime binding facade for P10/P11 gold-leakage-safe asset binding.

The implementation lives with asset synthesis because it is also used by
offline fixture tooling; this facade gives activation callers the explicit
runtime-boundary import named by the design document.
"""
from tehm.assets.synthesis import bind_asset_to_repair_context
from tehm.assets.receipts import RuntimeBindingReceipt

__all__ = ["RuntimeBindingReceipt", "bind_asset_to_repair_context"]
