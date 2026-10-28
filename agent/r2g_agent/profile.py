"""Flow parameters: r2g's measured defaults, or the user's own.

The defaults are the frozen baseline the nangate45 campaign ran 1249 designs
under (experiments/make_spec_table.py for the die and period,
experiments/conformance_check.py FROZEN for the SDC), so a design run with them
is sized and constrained exactly like the designs the knowledge base learned
from. A user may override any field; the merged result and the list of changed
fields are recorded with every design (metadata.json `flow_profile`) and in the
dataset index, so a dataset never mixes the two without saying so.

Every field is range-checked: a value outside its range is refused, never
clamped silently. PLACE_DENSITY_LB_ADDON keeps the skills' hard floor of 0.10
(placer divergence below it is irrecoverable).
"""
from __future__ import annotations

from . import platforms

# name: (label, unit, min, max, help)
FIELDS = {
    "period_ns": ("时钟周期", "ns", 0.05, 100.0,
                  "留空 = 按综合后单元数分档（经验值）；填一个数 = 所有设计都用这个周期"),
    "utilization": ("利用率", "", 0.20, 0.80,
                    "单元面积占 core 面积的比例。经验值 0.50：die 尺寸固定后，高于它的拥塞无法再修"),
    "area_allowance": ("面积余量", "×", 1.0, 2.0,
                       "综合面积没算进去的单元（tap、时钟缓冲、时序修复缓冲）的放大系数，实测值"),
    "pdn_floor_um": ("core 最小边长", "µm", 10.0, 2000.0, "电源网格能建起来的最小 core；小于它会 PDN-0185"),
    "die_side_um": ("固定 die 边长", "µm", 10.0, 20000.0,
                    "留空 = 按面积、引脚数和电源网格下限自动算；填一个数 = 所有设计都用这个 die"),
    "io_pct": ("输入输出延迟", "% 周期", 0.0, 50.0, "端口路径也纳入时序检查；0 则只约束寄存器之间"),
    "setup_uncertainty_pct": ("建立时间不确定度", "% 周期", 0.0, 30.0, "只加在 setup 上（加到 hold 会插入大量缓冲）"),
    "hold_uncertainty_ns": ("保持时间不确定度", "ns", 0.0, 1.0, "固定值，不随周期变化"),
    "place_density_lb_addon": ("放置密度加量", "", 0.10, 0.60,
                               "留空 = 不设（经验基线没设）；设了则不能低于 0.10"),
}


def defaults(platform: str) -> dict:
    p = platforms.get(platform)
    return {
        "period_ns": None,                       # None = the binned table
        "period_tiers": [list(b) for b in p.period_bins],
        "utilization": p.utilization,
        "area_allowance": p.area_allowance,
        "pdn_floor_um": p.pdn_floor_um,
        "die_side_um": None,                     # None = derived per design
        "io_pct": 15.0,
        "setup_uncertainty_pct": 5.0,
        "hold_uncertainty_ns": 0.020,
        "place_density_lb_addon": None,
    }


def merge(platform: str, overrides: dict | None) -> tuple[dict, list[str]]:
    """(profile, changed_fields). Raises ValueError naming the bad field."""
    prof = defaults(platform)
    changed = []
    for k, v in (overrides or {}).items():
        if k not in prof:
            raise ValueError("未知参数：%s" % k)
        if v in (None, ""):
            continue
        if k == "period_tiers":
            prof[k] = _tiers(v)
        else:
            lo, hi = FIELDS[k][2], FIELDS[k][3]
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError("%s 不是数字：%r" % (FIELDS[k][0], v)) from None
            if not lo <= x <= hi:
                raise ValueError("%s 超出范围 %g–%g：%g" % (FIELDS[k][0], lo, hi, x))
            prof[k] = x
        if prof[k] != defaults(platform)[k]:
            changed.append(k)
    return prof, changed


def _tiers(v) -> list:
    """[[upper_cells | None, period_ns], ...], increasing, last one open-ended."""
    try:
        out = [[None if u in (None, "") else int(u), float(ns)] for u, ns in v]
    except (TypeError, ValueError):
        raise ValueError("时钟分档格式应为 [[单元数上限, 周期ns], ..., [null, 周期ns]]") from None
    if not out or out[-1][0] is not None or any(u is None for u, _ in out[:-1]):
        raise ValueError("时钟分档的最后一档必须不设上限，其余各档必须有上限")
    uppers = [u for u, _ in out[:-1]]
    if uppers != sorted(set(uppers)) or any(ns <= 0 for _, ns in out):
        raise ValueError("时钟分档的上限要递增，周期要大于 0")
    return out


def period(prof: dict, cells: int) -> float:
    if prof.get("period_ns"):
        return float(prof["period_ns"])
    for upper, ns in prof["period_tiers"]:
        if upper is None or cells < upper:
            return float(ns)
    raise AssertionError("last tier must be open-ended")


def platform_view(platform: str, prof: dict) -> platforms.Platform:
    """The sizing rule set with this profile's numbers in it."""
    import dataclasses
    p = platforms.get(platform)
    return dataclasses.replace(p, utilization=prof["utilization"], area_allowance=prof["area_allowance"],
                               pdn_floor_um=prof["pdn_floor_um"])


def describe(platform: str) -> dict:
    """Defaults plus field metadata, for the UI and the model."""
    return {"platform": platform, "defaults": defaults(platform),
            "fields": {k: {"label": f[0], "unit": f[1], "min": f[2], "max": f[3], "help": f[4]}
                       for k, f in FIELDS.items()},
            "source": "%s 冻结基线：按平台采用已验证的尺寸、时钟和约束参数" % platforms.LABELS[platform]}
