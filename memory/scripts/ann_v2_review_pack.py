#!/usr/bin/env python3
"""Build a Chinese evidence companion, without changing annotations or signing audits.

Uses the EXISTING sample, quotes original files (not the enriched SOURCE raw_row),
and highlights missing/mismatched field provenance. No EDA/provider/runtime imports.
An existing output directory is refused so human review notes cannot be overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


TYPES = {'drc': '版图规则 DRC', 'drv': '电气限制 DRV', 'setup': '建立时间 setup',
         'hold': '保持时间 hold', 'stage_abort': '流程中止', 'route_residual': '布线残留',
         'lvs': '版图网表一致性 LVS', 'UNKNOWN': '类型未确定'}
EFFECTS = {'CLEARED': '目标已清除', 'IMPROVED': '有改善但未清除', 'NO_CHANGE': '无改善',
           'REGRESSED': '变差', 'UNKNOWN': '无法判断'}
NEW_FAILURES = {'KNOWN-none': '已有前后测量，程序未发现新增失败',
                'KNOWN-present': '程序发现新增失败', 'UNKNOWN': '证据不足，无法判断',
                'NOT_MEASURED_BEFORE': '动作前未测量，不能判断是否新增'}
ROW_KEYS = ('fix_session_id', 'iter', 'strategy', 'check', 'violation_class', 'from_stage',
            'before', 'after', 'verdict', 'rolled_back', 'rollback_reason', 'config_delta',
            'before_categories', 'global_regressions', 'situation', 'ts')
METRICS = ('finish__timing__setup__ws', 'finish__timing__setup__tns',
           'finish__timing__hold__ws', 'finish__timing__drv__max_slew',
           'finish__timing__drv__max_cap', 'finish__timing__drv__max_fanout')


def j(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def cell(value):
    return j(value).replace('|', '&#124;').replace('\n', ' ')[:1400]


def link(path, line=None):
    target = str(path) + (f':{line}' if line else '')
    return f'[{Path(path).name}' + (f':{line}' if line else '') + f'](<{target}>)'


def quote(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    # Do not let literal fences in an upstream string break the evidence block.
    fence = '`' * max(3, max((len(x) for x in re.findall(r'`+', text)), default=0) + 1)
    return f'{fence}json\n{text}\n{fence}'


class Sources:
    def __init__(self):
        self.cache = {}

    def get(self, path):
        path = str(path)
        if path not in self.cache:
            try:
                raw = Path(path).read_bytes()
                self.cache[path] = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                                    'text': raw.decode('utf-8', errors='replace'), 'error': None}
            except OSError as exc:
                self.cache[path] = {'path': path, 'sha256': None, 'text': None,
                                    'error': type(exc).__name__}
        return self.cache[path]

    def obj(self, path):
        item = self.get(path)
        try:
            return json.loads(item['text']) if item['text'] is not None else None
        except ValueError:
            return None


def lookup(obj, key):
    if isinstance(obj, dict) and key in obj:
        return True, obj[key]
    cur = obj
    for part in key.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def extract_reference(ref, sources):
    item = sources.get(ref['path'])
    expected = ref.get('sha256')
    result = {'path': ref['path'], 'locator': ref.get('locator', ''), 'value': None,
              'sha256': item['sha256'], 'expected_sha256': expected,
              'hash_status': ('UNAVAILABLE' if item['sha256'] is None else
                              'NOT_RECORDED' if not expected else
                              'MATCH' if expected == item['sha256'] else 'MISMATCH')}
    if item['error']:
        return {**result, 'state': 'UNREADABLE', 'error': item['error']}
    text, loc = item['text'], str(ref.get('locator', ''))
    lines = text.splitlines()
    match = re.fullmatch(r'line (\d+)(?::\s*(.*))?', loc)
    if match:
        number = int(match[1]); result['line'] = number
        if not 1 <= number <= len(lines):
            return {**result, 'state': 'MISSING_LINE'}
        try:
            obj = json.loads(lines[number - 1])
        except ValueError:
            return {**result, 'state': 'TEXT', 'value': lines[number - 1]}
        if not match[2]:
            return {**result, 'state': 'VALUE', 'value': obj}
        found, value = lookup(obj, match[2])
        return {**result, 'state': 'VALUE' if found else 'MISSING_FIELD', 'value': value}
    match = re.fullmatch(r'tail-500 line (\d+)', loc)
    if match:
        number = max(0, len(lines) - 500) + int(match[1])
        return {**result, 'line': number, 'state': 'TEXT' if 1 <= number <= len(lines) else 'MISSING_LINE',
                'value': lines[number - 1] if 1 <= number <= len(lines) else None}
    obj = sources.obj(ref['path'])
    found, value = lookup(obj, loc)
    if found:
        return {**result, 'state': 'VALUE', 'value': value}
    if loc == 'finish__ prefix' and isinstance(obj, dict):
        return {**result, 'state': 'DERIVED', 'value': {k: obj[k] for k in METRICS if k in obj}}
    if loc.startswith('all lines'):
        return {**result, 'state': 'TEXT', 'value': lines[:8]}
    return {**result, 'state': 'MISSING_FIELD'}


def load_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    out = {r['unit_id']: r for r in rows}
    if len(out) != len(rows):
        raise ValueError(f'duplicate unit_id in {path}')
    return out


def original_rows(unit, sources):
    out = []
    for ref in unit.get('provenance', {}).get('sources', []):
        if not str(ref.get('path', '')).endswith('fix_log.jsonl'):
            continue
        out.append(extract_reference({'path': ref['path'], 'locator': f"line {ref['line']}"}, sources))
    return out


def field_evidence(record, sources):
    rows, findings = [], []
    fields = [('rolled_back', record.get('rolled_back', {}))]
    for target in record.get('targets', []):
        fields.extend((target['type'] + '.' + key, value) for key, value in target['fields'].items()
                      if value.get('status') != 'NOT_APPLICABLE')
    for name, field in fields:
        refs = field.get('source') or []
        if field.get('status') == 'KNOWN' and not refs:
            findings.append(f'{name} 标成 KNOWN，但没有来源引用')
        evidence = []
        for ref in refs:
            ex = extract_reference(ref, sources)
            # A derived label is not expected to equal the scalar at its source.
            # Preserve the original value and label the derivation for human review.
            if name.endswith('.rule_class') and 'before_categories' in ex['locator']:
                ex['derivation'] = 'rule_class 由原文分类向量推导；不能把整个向量与类别名直接比较'
            elif name == 'stage_abort.timeout' and ex['locator'].endswith(': status'):
                ex['derivation'] = 'timeout 由原文退出码推导；退出码本身不是布尔值'
            elif name in ('setup.metric', 'hold.metric'):
                ex['derivation'] = 'metric 名称由原始指标键名推导；原文数字是指标值，不是指标名称'
            evidence.append(ex)
            if ex['hash_status'] == 'MISMATCH':
                findings.append(f'{name} 的原文件哈希与标注引用不一致')
            if field.get('status') != 'KNOWN':
                continue
            if ex['state'] in ('UNREADABLE', 'MISSING_LINE', 'MISSING_FIELD'):
                detail = '原始日志未记录 rolled_back' if name == 'rolled_back' and ex['state'] == 'MISSING_FIELD' else ex['state']
                findings.append(f'{name} 来源无法直接支持标注：{detail}')
            elif name == 'rolled_back' and (type(ex['value']) is not bool or ex['value'] != field.get('value')):
                findings.append('rolled_back 的原始值与标注值不一致（不能将缺失、null 或字符串当作 false）')
            elif not ex.get('derivation') and ex['state'] == 'VALUE' and not isinstance(ex['value'], (dict, list)) and ex['value'] != field.get('value'):
                findings.append(f'{name} 的原始值与程序标注值不一致')
        rows.append((name, field, evidence))
    return rows, list(dict.fromkeys(findings))


def find_run_paths(binding, unit):
    if binding.get('status') != 'KNOWN':
        return []
    paths = list(((binding.get('evidence') or {}).get('e1') or {}).get('paths') or [])
    for ref in unit.get('provenance', {}).get('sources', []):
        if str(ref.get('path', '')).endswith('fix_log.jsonl'):
            p = Path(ref['path']).parents[1] / 'backend' / binding['run']
            if p.is_dir():
                paths.append(str(p))
    return list(dict.fromkeys(paths))


def run_section(label, binding, unit, sources):
    lines = [f'### {label}运行：程序绑定状态 {binding.get("status")}', '']
    if binding.get('status') != 'KNOWN':
        lines += ['程序没有确定唯一运行。原因：`' + str(binding.get('unknown_reason')) + '`。',
                  '候选运行（不是已确认归属）：', quote([x.get('run') for x in binding.get('candidates', [])]), '']
        return lines
    paths = find_run_paths(binding, unit)
    lines += [f'运行编号：`{binding["run"]}`。以下摘录不代表人工确认绑定正确。',
              '程序给出的绑定依据：', quote(binding.get('evidence')), '']
    if not paths:
        return lines + ['未找到可读取的运行目录，需要补证据。', '']
    run = Path(paths[0])
    lines += ['本次摘录目录：' + link(run), '其他副本：' + ('；'.join(link(p) for p in paths[1:]) or '无'), '']
    for rel, keys in (
        ('resume_meta.json', ('reason', 'from_stage', 'parent_lineage')),
        ('drc/drc_result.json', ('status', 'violations', 'checker')),
        ('lvs/netgen_lvs_result.json', ('status', 'tool', 'mismatch_class')),
        ('logs/6_report.json', METRICS),
    ):
        path = run / rel; obj = sources.obj(path)
        if not isinstance(obj, dict):
            lines += [f'- `{rel}`：未读取到可用 JSON，不能据此认为检查通过。']
        else:
            lines += ['', link(path) + '（原文键值摘录）：', quote({k: obj[k] for k in keys if k in obj})]
    for rel in ('stage_log.jsonl', 'stage_artifact_manifest.jsonl'):
        path = run / rel; item = sources.get(path)
        if item['text'] is None:
            lines += [f'- `{rel}`：无法读取。']; continue
        values = []
        for number, line in enumerate(item['text'].splitlines(), 1):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            values.append({'line': number, **{k: obj[k] for k in
                          ('stage', 'status', 'sha256', 'artifact', 'path') if k in obj}})
        lines += ['', link(path) + '（阶段、状态及产物哈希摘录）：', quote(values)]
    rpt = run / 'reports_orfs/5_route_drc.rpt'
    item = sources.get(rpt)
    if item['text'] is not None:
        lines += ['', link(rpt) + '（前 12 行；空文件明确显示为空，不自行推定验收通过）：',
                  quote(item['text'].splitlines()[:12])]
    lines += ['']
    return lines


def quick_comparison(record, unit, sources):
    """A compact front page made from original run files, not annotation claims."""
    columns = []
    for side in ('pre', 'post'):
        paths = find_run_paths(record['binding'][side], unit)
        run = Path(paths[0]) if paths else None
        if run is None:
            columns.append({}); continue
        values = {}
        for rel, keys in (
            ('drc/drc_result.json', ('violations',)),
            ('lvs/netgen_lvs_result.json', ('status',)),
            ('logs/6_report.json', METRICS),
        ):
            obj = sources.obj(run / rel)
            if isinstance(obj, dict):
                values.update({k: obj[k] for k in keys if k in obj})
        stage = sources.get(run / 'stage_log.jsonl')
        if stage['text']:
            try:
                last = json.loads(stage['text'].splitlines()[-1])
                values['last_stage'] = {k: last.get(k) for k in ('stage', 'status')}
            except ValueError:
                pass
        columns.append(values)
    labels = [('最后记录的流程阶段与退出码', 'last_stage'), ('DRC 违规数', 'violations'),
              ('LVS 原始状态', 'status'), ('建立时间最差裕量（原始值，单位见证据）', METRICS[0]),
              ('保持时间最差裕量（原始值，单位见证据）', METRICS[2]),
              ('最大 slew 违规数', METRICS[3]), ('最大 cap 违规数', METRICS[4]),
              ('最大 fanout 违规数', METRICS[5])]
    lines = ['## 关键原文值，已经替你定位', '',
             '下表直接摘自上述绑定目录中的原文件；尚需确认绑定是否正确。缺失绝不填 0。', '',
             '| 原始指标 | 修复前 | 修复后 |', '| --- | --- | --- |']
    for label, key in labels:
        a, b = [cell(col[key]) if key in col else '未读到／未绑定' for col in columns]
        lines.append(f'| {label} | {a} | {b} |')
    lines += ['', '布尔值 false 表示“未回滚”，null／缺失表示没有该值；这两个不能互换。', '']
    return lines


def render_card(number, record, unit, stratum, sources):
    raw = unit.get('raw_row') or {}
    design = (unit.get('identity') or {}).get('design') or '?'
    evidence, findings = field_evidence(record, sources)
    original = original_rows(unit, sources)
    for row in original:
        if row['state'] != 'VALUE' or not isinstance(row['value'], dict):
            findings.append('原始动作日志行无法读取，无法核对本次动作')
    findings = list(dict.fromkeys(findings))
    effects = '；'.join(TYPES.get(k, k) + '：' + EFFECTS.get(v, v) for k, v in record.get('step_effect', {}).items())
    lines = [f'# {number:03d}｜{design}｜{raw.get("strategy", "?")}', '',
             '[返回审阅入口](../README.md)', '',
             '**人工结论未填写。所有“程序结论”都是待核对的主张，不是本材料替你签核。**', '',
             f'记录 ID：`{record["unit_id"]}`。原抽样分层：`{stratum}`。', '',
             '## 先看这一页需要判断什么', '',
             '| 问题 | 程序声称 |', '| --- | --- |',
             f'| 当时做了什么 | `{raw.get("strategy")}`，第 {raw.get("iter")} 步 |',
             f'| 动作效果 | {effects} |',
             f'| 是否回滚 | {cell(record.get("rolled_back", {}).get("value"))}；状态 {record.get("rolled_back", {}).get("status")} |',
             f'| 是否计入正向经验 | {"是，但仅为局部单步证据" if record.get("positive") else "否；不是直接判定整个任务失败"} |',
             f'| 原因／阻挡项 | {cell(record.get("positive_blockers", []))} |', '',
             '## 自动发现的待核实问题', '']
    lines += ['- ' + x for x in findings] if findings else ['未发现本工具能够识别的字段来源问题；**并不表示审阅通过**，运行归属及推导仍须核对。']
    lines += [''] + quick_comparison(record, unit, sources)
    lines += ['<details>', '<summary>展开原始证据片段、字段来源及运行绑定细节（无需自己找文件）</summary>', '',
              '## 原始日志与导出补充值分开看', '',
              '下面原始行来自磁盘日志，不是 SOURCE-v1 的 enriched raw_row；仅摘录审阅相关字段。', '']
    for row in original:
        lines += [link(row['path'], row.get('line')) + '：']
        if row['state'] == 'VALUE' and isinstance(row['value'], dict):
            values = {k: row['value'][k] for k in ROW_KEYS if k in row['value']}
            lines += [quote(values)]
            if 'rolled_back' not in row['value']:
                lines += ['**原始日志未记录 rolled_back。缺失不是 false，也不证明发生了回滚。**']
        else:
            lines += [f'无法读取：{row["state"]}。']
    lines += ['', '导出记录中的值（不是原日志原文）：',
              quote({'rolled_back_in_export_raw_row': raw.get('rolled_back'),
                     'rollback_restoration': raw.get('rollback_restoration'), 'rollback': unit.get('rollback')}),
              '若上面有恢复审查／推定证据，应单独追溯；不能标成原日志直接记载。', '']
    lines += run_section('修复前', record['binding']['pre'], unit, sources)
    lines += run_section('修复后', record['binding']['post'], unit, sources)
    lines += ['## 程序字段与原始证据逐项对照', '',
              '哈希一致只证明引用的是同一文件，不证明该文件里有这个字段。派生字段仍须解释推导。', '']
    for name, field, refs in evidence:
        lines += [f'### `{name}`：程序值 {cell(field.get("value"))}，状态 {field.get("status")}', '']
        if not refs:
            lines += ['没有字段级来源引用；未知字段保留未知，已知字段需要补证据。', '']; continue
        for ex in refs:
            lines += [link(ex['path'], ex.get('line')) + f'；定位 `{ex["locator"]}`；哈希核对 `{ex["hash_status"]}`。']
            if ex.get('derivation'):
                lines += [ex['derivation'] + '。以下仍保留原文值，不替换成程序推导值。']
            if ex['state'] in ('VALUE', 'TEXT', 'DERIVED'):
                value = ex['value']
                if isinstance(value, dict) and name.endswith('rule_class'):
                    lines += ['这里是原始分类向量，rule_class 是从该向量推导，不是同名直接字段。']
                lines += [quote(value)]
            else:
                lines += [f'**未读到对应原文值：{ex["state"]}。不得以程序值填补原文。**']
        lines += ['']
    lines += ['## 其他检查与来源身份', '', '| 检查 | 程序判定的含义 |', '| --- | --- |']
    lines += [f'| {TYPES.get(k, k)} | {NEW_FAILURES.get(v, v)} |' for k, v in record.get('new_failures', {}).items()]
    lines += ['', '身份字段是来源表推导，需要核对 family/source_group 定义；不因修复效果好坏改变：',
              quote(record.get('identity')), '', '</details>', '',
              '## 你填写的审阅结论', '',
              '每项填写“一致／有问题／无法判断”，不要把错误类型勾选框当作通过按钮。', '',
              '| 核对项 | 你的结论 | 依据或疑问 |', '| --- | --- | --- |',
              '| 前后运行是否属于本次动作 | 未填写 | |',
              '| KNOWN 字段是否确有对应证据 | 未填写 | |',
              '| 局部效果和正向计数是否有依据 | 未填写 | |',
              '| 回滚是否遗漏／来源是否错标 | 未填写 | |',
              '| 设计、IP 和来源簇是否正确 | 未填写 | |', '',
              'HUMAN_VERDICT: 未填写', '',
              '可填 PASS（证据支持本条标注）／FAIL（发现错误）／BLOCKED（证据不足，不能判断）。',
              '审阅人：未填写', '', '意见／需要补充的证据：', '']
    return '\n'.join(lines), findings


def build_package(annotation, source, sample, out):
    annotation, source, sample, out = map(Path, (annotation, source, sample, out))
    if out.exists():
        raise FileExistsError(f'Refuse to overwrite human review notes: {out}')
    records, units = load_rows(annotation), load_rows(source)
    draw = json.loads(sample.read_text())
    ids = [uid for group in draw['by_stratum'].values() for uid in group]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate sample unit_id')
    missing = set(ids) - (records.keys() & units.keys())
    if missing:
        raise ValueError(f'missing sampled records: {sorted(missing)}')
    sources = Sources(); cards = []
    for stratum, group in draw['by_stratum'].items():
        for uid in group:
            number = len(cards) + 1
            text, findings = render_card(number, records[uid], units[uid], stratum, sources)
            filename = f'{number:03d}__{hashlib.sha256(uid.encode()).hexdigest()[:12]}.md'
            cards.append({'unit_id': uid, 'number': number, 'file': 'cards/' + filename,
                          'design': units[uid].get('identity', {}).get('design'), 'stratum': stratum,
                          'findings': findings, 'text': text})
    # Reading and rendering finish before creating anything; the source sample is unchanged.
    out.mkdir(parents=True, exist_ok=False); (out / 'cards').mkdir()
    for card in cards:
        (out / card['file']).write_text(card['text'])
    flagged = sum(bool(c['findings']) for c in cards)
    index = ['# ann-v2 中文证据对照审阅入口', '',
             f'沿用原固定样本：{len(cards)} 条，seed={draw.get("seed")}。没有重抽样、修改标注或替人签核。', '',
             '## 怎么用', '',
             '1. 打开下面的一条记录；原始动作、前后结果及字段证据片段已经摘录在同页。',
             '2. 先看“自动发现的待核实问题”，再对照程序结论和原文。文件链接用于追溯，不必先自行寻找目录。',
             '3. 在每条末尾填写五项判断和 HUMAN_VERDICT。没有自动填写的 PASS／FAIL。',
             '4. 自动比对没有发现问题，并不表示审阅通过；UNKNOWN 也不代表修复失败。',
             '5. 当前材料仅为 DEV 审阅辅助件，不改变 224 条正向计数，不使 ann-v2 获准冻结。', '',
             f'自动发现字段来源疑点的样本：{flagged}/{len(cards)}。这是待核实提示，不是人工确认的错误率。', '',
             '已知重点：导出阶段补入的 rolled_back 不能冒充原始日志里的 rolled_back；',
             '缺少回滚字段既不等于 false，也不等于 true。恢复审查需单独提供来源并按规程判断。', '',
             '## 建议先看的来源疑点', '', '| 序号／设计 | 待核实提示 |', '| --- | --- |']
    for card in cards:
        if card['findings']:
            index.append(f'| [{card["number"]:03d} {card["design"]}]({card["file"]}) | ' +
                         '; '.join(card['findings']).replace('|', '&#124;') + ' |')
    index += ['', '## 全部固定样本（原顺序）', '', '| 序号／设计 | 原抽样分层 | 自动提示 | 人工状态 |', '| --- | --- | --- | --- |']
    for card in cards:
        index.append(f'| [{card["number"]:03d} {card["design"]}]({card["file"]}) | '
                     f'{card["stratum"].replace("|", " / ")} | {len(card["findings"])} 项 | 未填写 |')
    index += ['', '原审阅工作表和抽样清单保持不变。本目录拒绝覆盖已有内容，避免丢失你的审阅意见。', '']
    (out / 'README.md').write_text('\n'.join(index))
    manifest = {'purpose': 'evidence_companion_only', 'human_audit_status': 'NOT_REVIEWED',
                'sampled_units': len(cards), 'units_with_findings': flagged,
                'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (annotation, source, sample)},
                'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'cards': [{k: v for k, v in c.items() if k != 'text'} for c in cards],
                'evidence_files': [{k: v for k, v in item.items() if k != 'text'}
                                   for _, item in sorted(sources.cache.items())]}
    (out / 'evidence_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('annotation', 'source', 'sample', 'out'):
        parser.add_argument('--' + arg, type=Path, required=True)
    args = parser.parse_args()
    result = build_package(args.annotation, args.source, args.sample, args.out)
    print(j({k: result[k] for k in ('sampled_units', 'units_with_findings', 'human_audit_status')}))
