#!/usr/bin/env python3
"""Build the requested three-platform handoff on 203, verifying original RTL."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from collect_evidence import copy_stop, copy_small


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selection', type=Path, required=True)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--remote-evidence', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.selection.read_text())
    receipts = []
    for row in data['selected']:
        dest = args.stage / row['platform'] / row['id']
        if dest.exists():
            raise FileExistsError(dest)
        dest.mkdir(parents=True)
        source = Path(row['candidate']['input_root'])
        expected = row['candidate'].get('source_sha256', {})
        for rel, digest in expected.items():
            p = source / rel
            if not p.is_file() or sha(p) != digest:
                raise RuntimeError('Original source hash mismatch: ' + str(p))
        shutil.copytree(source, dest / 'original')
        if row['stop_host'] == '203':
            copy_stop(Path(row['stop_project']), dest / 'r2g_stop')
            copy_small(Path(row['job_receipt']), dest / 'r2g_stop/job_receipt.json')
        evidence = args.remote_evidence / row['platform'] / row['id']
        if evidence.exists():
            shutil.copytree(evidence, dest, dirs_exist_ok=True)
        for rel, digest in expected.items():
            if sha(dest / 'original' / rel) != digest:
                raise RuntimeError('Copied source hash mismatch: ' + rel)
        # Keep the exact recorded config as evidence; the runnable copy resolves
        # only this project's paths so it can be used after transfer to zhangdy.
        cfg = dest / 'r2g_stop/constraints/config.mk'
        text = cfg.read_text()
        cfg.with_name('config.recorded.mk').write_text(text)
        text = text.replace(row['stop_project'], '$(HANDOFF_ROOT)/r2g_stop')
        text = text.replace(row['candidate']['input_root'], '$(HANDOFF_ROOT)/original')
        cfg.write_text(text)
        original_cfg = text.replace('$(HANDOFF_ROOT)/r2g_stop/rtl', '$(HANDOFF_ROOT)/original')
        c = row['candidate']
        filelist = ' '.join('$(HANDOFF_ROOT)/original/' + rel for rel in c['source_files'])
        includes = ' '.join('$(HANDOFF_ROOT)/original/' + rel for rel in c.get('include_dirs', ['.']))
        original_cfg = re.sub(r'^export\s+VERILOG_FILES\s*=.*$', 'export VERILOG_FILES = ' + filelist, original_cfg, flags=re.M)
        original_cfg = re.sub(r'^export\s+VERILOG_INCLUDE_DIRS\s*=.*$', 'export VERILOG_INCLUDE_DIRS = ' + includes, original_cfg, flags=re.M)
        (dest / 'original/config.mk').write_text(original_cfg)
        script = '''#!/usr/bin/env bash
set -euo pipefail
export HANDOFF_ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE=${1:-r2g_stop}; GOAL=${2:-synth}
case "$MODE" in
  original) CONFIG="$HANDOFF_ROOT/original/config.mk" ;;
  r2g_stop) CONFIG="$HANDOFF_ROOT/r2g_stop/constraints/config.mk" ;;
  *) echo 'usage: reproduce.sh [original|r2g_stop] [synth|finish|other-make-target]' >&2; exit 2 ;;
esac
: "${ORFS_ROOT:?Set ORFS_ROOT to your OpenROAD-flow-scripts checkout and load its tool environment first}"
WORK="$HANDOFF_ROOT/work/$MODE"
mkdir -p "$WORK"
exec make -C "$ORFS_ROOT/flow" DESIGN_CONFIG="$CONFIG" \\
  WORK_HOME="$WORK" LOG_DIR="$WORK/logs" REPORTS_DIR="$WORK/reports" \\
  RESULTS_DIR="$WORK/results" OBJECTS_DIR="$WORK/objects" NUM_CORES="${NUM_CORES:-8}" "$GOAL"
'''
        (dest / 'reproduce.sh').write_text(script)
        (dest / 'reproduce.sh').chmod(0o755)
        issue = (row.get('stop_detail') or {}).get('reason') or str(row.get('symptom'))
        (dest / 'HANDOFF.json').write_text(json.dumps(row, ensure_ascii=False, indent=2))
        size = f"{row['max_observed_cells']} 个单元（记录的最大值）" if row['max_observed_cells'] is not None else '综合失败，单元数未知；不得据此认为规模小'
        note = ('已有模型修复成功记录，但尚无本次筛选所依据的有效 recipe；可优先排查。' if row['priority'].startswith('1_') else
                '需恢复源仓库中的真实存储器初始化文件；不能用伪造或清零内容替代。' if row['priority'].startswith('3_') else
                '尚无有效 recipe；需要诊断根因，不能保证修复成功。')
        (dest / 'README.md').write_text(f'''# {row['id']}: {c.get('top')}

平台：{row['platform']}；来源设计：{c.get('design')}。
规模：{size}。
当前停止原因：{issue}

{note}

- `original/`：实验三原始输入；RTL 已与冻结 manifest 的 SHA256 核对。
- `r2g_stop/`：R2G 停止时的 RTL、约束、报告和日志；保留其自动 frontend 处理，与原始输入区分。
- `baseline/`：原基线的配置、结果和日志，供诊断，不是新的成功结果。
- `HANDOFF.json`：来源、失败分类、recipe 状态、规模依据及已有模型修复记录。
- `reproduce.sh`：设置 ORFS_ROOT 并加载 EDA 环境后，运行 `bash reproduce.sh r2g_stop synth`；布局布线可用 `finish`。`original` 模式从未经 R2G frontend 处理的源文件开始。

`config.recorded.mk` 保留历史绝对路径，`config.mk` 使用 HANDOFF_ROOT 定位本目录。输入中的历史元数据也可能保留来源路径，不是交付依赖。
后续修复应保持原 RTL 功能、完整 DRC/LVS 条件；本包只交付排查材料，未重跑流程。
''')
        # The target is another user's shared directory. Give that named user
        # access to the new bundle without changing the existing directory ACL.
        subprocess.run(['setfacl', '-R', '-m', 'u:zhangdy:rwX', str(dest)], check=True)
        for directory in [dest, *[p for p in dest.rglob('*') if p.is_dir()]]:
            subprocess.run(['setfacl', '-m', 'd:u:zhangdy:rwx', str(directory)], check=True)
        subprocess.run(['bash', '-n', str(dest / 'reproduce.sh')], check=True)
        files = {str(p.relative_to(dest)): {'sha256': sha(p), 'bytes': p.stat().st_size}
                 for p in sorted(dest.rglob('*')) if p.is_file()}
        (dest / 'FILES.json').write_text(json.dumps(files, indent=2))
        subprocess.run(['setfacl', '-m', 'u:zhangdy:rw', str(dest / 'FILES.json')], check=True)
        receipts.append({'id': row['id'], 'platform': row['platform'], 'files': len(files) + 1,
                         'original_rtl_hashes_verified': len(expected), 'size_status': row['size_status'],
                         'priority': row['priority'], 'bytes': sum(v['bytes'] for v in files.values())})
        print(row['id'], 'verified', len(expected), flush=True)
    result = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'scope': 676,
              'selected': len(receipts), 'platform_counts': dict(Counter(r['platform'] for r in receipts)),
              'excluded_counts': data['excluded_counts'], 'selection_rule': data['selection'],
              'rows': receipts}
    (args.stage / 'HANDOFF_MANIFEST_20261115.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    shutil.copyfile(args.selection, args.stage / 'SELECTION_20261115.json')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
