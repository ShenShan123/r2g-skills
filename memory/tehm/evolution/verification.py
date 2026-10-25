"""Compatibility facade for learner verified-execution admission."""
from tehm.verified_execution import (
    require_verified_execution, require_verified_transition,
)

__all__ = ["require_verified_execution", "require_verified_transition"]
