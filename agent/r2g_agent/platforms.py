"""Per-platform sizing and timing rules.

These are the rules the nangate45 frozen-spec campaign validated on 1249
designs (experiments/make_spec_table.py, the one place they were derived), so a
design entering through the agent is sized exactly like the designs the
knowledge base learned from:

  * clock period from a binned table on post-synthesis cell count,
  * utilization 50 % after a measured area allowance for cells synthesis never
    counts (taps, clock buffers, timing-repair buffers),
  * a core no smaller than the IO ring needs (place_pins positions per um of
    core perimeter) and no smaller than the PDN grid needs,
  * a 2 um inset between die and core.

Adding a platform means adding an entry here; nothing else in the agent is
platform-specific.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    name: str
    period_bins: tuple            # ((upper_cells_exclusive | None, period_ns), ...)
    area_allowance: float
    utilization: float
    io_pos_per_um: float
    pdn_floor_um: float
    core_inset_um: float = 2.0
    port_buffer_um2: float = 0.0  # area buffer_ports adds per IO pin


PLATFORMS = {
    "nangate45": Platform(
        name="nangate45",
        period_bins=((100, 0.5), (1000, 1.0), (5000, 1.6), (None, 3.0)),
        area_allowance=1.021,
        utilization=0.5,
        io_pos_per_um=1.80,
        pdn_floor_um=32.0,
    ),
}


def get(name: str) -> Platform:
    try:
        return PLATFORMS[name]
    except KeyError:
        raise ValueError("platform %r is not supported in this version (supported: %s)"
                         % (name, ", ".join(sorted(PLATFORMS)))) from None


def period_for(p: Platform, cells: int) -> float:
    for upper, ns in p.period_bins:
        if upper is None or cells < upper:
            return ns
    raise AssertionError("last bin must have upper=None")


def die_for(p: Platform, cell_area_um2: float, pins: int) -> dict:
    """Square die from area, pins and the PDN floor; returns sides in um."""
    eff = max(cell_area_um2 * p.area_allowance, cell_area_um2 + pins * p.port_buffer_um2)
    derived = math.sqrt(eff / p.utilization) if eff > 0 else 0.0
    pin_need = pins / (p.io_pos_per_um * 4.0) if pins > 0 else 0.0
    core = math.ceil(max(derived, pin_need, p.pdn_floor_um))
    return {
        "core_side_um": core,
        "die_side_um": core + 2 * p.core_inset_um,
        "core_inset_um": p.core_inset_um,
        "binding": ("pins" if pin_need >= max(derived, p.pdn_floor_um)
                    else "pdn_floor" if p.pdn_floor_um >= derived else "area"),
    }
