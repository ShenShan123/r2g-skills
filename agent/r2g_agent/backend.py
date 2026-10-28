"""Bounded compatibility repairs for diagnosed ORFS tool failures.

These do not edit RTL, clocks, DRC decks, or LVS acceptance. Every retry still
has to complete the real flow and normal signoff. Historical case IDs are not
part of selection.
"""
import re
from pathlib import Path

from .paths import signoff_scripts


def candidate(project: Path, platform: str, log: str) -> dict | None:
    if platform not in ('sky130hd', 'nangate45', 'gf180'):
        return None
    config = (project / 'constraints/config.mk').read_text()
    if '[ERROR RSZ-0074]' in log and 'Failed to build tree from global routes' in log:
        if re.search(r'^\s*(?:export\s+)?SKIP_INCREMENTAL_REPAIR\s*=', config, re.M):
            return None
        return {'recipe': 'skip_incremental_repair', 'from_stage': 'route',
                'config_edits': {'SKIP_INCREMENTAL_REPAIR': '1'},
                'detail': '增量全局布线树重建失败；跳过该可选优化，重新布线并正常签核'}
    if re.search(r'\[ERROR STA-1670\].*6_final\.spef.*syntax error', log):
        if re.search(r'^\s*(?:export\s+)?PRE_FINAL_REPORT_TCL\s*=', config, re.M):
            return None
        spefs = sorted(project.glob('backend/RUN_*/results/6_final.spef'),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not spefs or not re.search(r'(?<!\\)\[\\?-\d+\]', spefs[0].read_text(errors='replace')):
            return None
        hook = signoff_scripts() / 'flow/orfs_hooks/spef_name_encoding.tcl'
        return {'recipe': 'spef_name_encoding', 'from_stage': 'finish',
                'config_edits': {'PRE_FINAL_REPORT_TCL': str(hook)},
                'detail': 'SPEF 负下标名称编码不兼容；仅规范转义，保留全部连接和RC值'}
    return None


def apply(project: Path, repair: dict) -> None:
    path = project / 'constraints/config.mk'
    text = path.read_text()
    for name, value in repair['config_edits'].items():
        if re.search(r'^\s*(?:override\s+)?(?:export\s+)?' + re.escape(name) + r'\s*[:?+]?=', text, re.M):
            raise ValueError('refuse to replace an existing backend hook: ' + name)
        text += '\nexport ' + name + ' = ' + value + '\n'
    path.write_text(text)
