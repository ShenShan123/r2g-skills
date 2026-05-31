#!/usr/bin/env python3
"""Student-facing pre-submission self-check.

Runs the same checks as the autograder (``verify_submission.py``) against your
own teaching_root and prints a friendly checklist, so you can fix problems
*before* submitting. It changes nothing and grades nothing — it just tells you
what the autograder will flag.

    python3 check_my_case.py [--teaching-root PATH]

If --teaching-root is omitted, it assumes this script's directory chain can
locate it; otherwise pass the directory that contains TEACHING_POLICY.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from verify_submission import (  # noqa: E402
    _CASE_STATE_ARTIFACT_SUBDIR,
    parse_case_state,
    verify_submission,
)

GREEN = "\033[32m"
RED = "\033[31m"
YEL = "\033[33m"
RST = "\033[0m"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--teaching-root", type=Path, default=None)
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    troot = args.teaching_root
    if troot is None:
        # try cwd, then walk up looking for TEACHING_POLICY.md
        cur = Path.cwd()
        for cand in [cur, *cur.parents]:
            if (cand / "TEACHING_POLICY.md").is_file():
                troot = cand
                break
    if troot is None or not (troot / "TEACHING_POLICY.md").is_file():
        print("找不到 teaching_root（含 TEACHING_POLICY.md 的目录）。"
              "请用 --teaching-root 指定。", file=sys.stderr)
        return 2

    g, r, y, rst = ("", "", "", "") if args.no_color else (GREEN, RED, YEL, RST)

    verdicts, ledger_notes = verify_submission(troot)
    if not verdicts:
        print(f"{y}没有发现任何已记录状态的 case。先跑 Stage 1 再来自检。{rst}")
        return 0

    by_design: dict[str, list] = {}
    for v in verdicts:
        by_design.setdefault(v.design, []).append(v)

    total_flags = 0
    for design, vs in by_design.items():
        print(f"\n=== {design} ===")
        for v in vs:
            mark = f"{g}OK{rst}" if v.ok else f"{r}标红{rst}"
            print(f"  [{mark}] {v.stage}: {v.final_status}")
            for c in v.checks_failed:
                total_flags += 1
                print(f"        {r}- {c}{rst}")

    # ── 账本：拆分无害软提示 / 建议修正 / 真正的问题 ─────────────────────
    SOFT_NOTE_PREFIXES = (
        "ledger_absent",
        "output_superseded",
        "repo_root_unresolved",
        "unresolvable_output_path",
        "unresolvable_inputs_path",
        "input_missing",
        "input_unreadable",
        "input_hash_mismatch",
    )
    ADVICE_NOTE_PREFIXES = (
        "artifact_not_in_ledger",
        "case_state_path_should_be_teaching_root",
    )
    ledger_soft: list[str] = []
    ledger_advice: list[str] = []
    ledger_hard: list[str] = []
    for note in ledger_notes:
        if any(note.startswith(p) for p in ADVICE_NOTE_PREFIXES):
            ledger_advice.append(note)
        elif any(note.startswith(p) for p in SOFT_NOTE_PREFIXES):
            ledger_soft.append(note)
        else:
            ledger_hard.append(note)

    print("\n--- 账本 ---")
    if ledger_hard:
        print(f"  {r}账本问题：{', '.join(ledger_hard)}{rst}")
    elif ledger_notes == ["ledger_absent"]:
        print(f"  {y}未发现 run_ledger.jsonl。"
              f"若本课程要求账本，请确认用 run_stage.sh 跑流程（它会自动写账本）。{rst}")
    elif not ledger_notes:
        print(f"  {g}账本链校验通过。{rst}")
    else:
        # 只有软提示，无硬问题
        print(f"  {g}账本链校验通过。{rst}")
    if ledger_soft:
        print(f"  {y}提示：{', '.join(ledger_soft)}{rst}")
        if any(n.startswith("repo_root_unresolved") for n in ledger_soft):
            print(f"  {y}      账本中部分 <repo> 路径产物未做哈希校验"
                  f"（批改端未提供 --repo-root），属正常，不影响提交。{rst}")
        if any(n.startswith("output_superseded") for n in ledger_soft):
            print(f"  {y}      output_superseded 是 latest-wins 重跑留下的旧产物记录，"
                  f"属正常软提示，不影响提交。{rst}")
    if ledger_advice:
        print(f"  {y}建议修正/需复核：{', '.join(ledger_advice)}{rst}")
        for design, key, new_path in _case_state_path_suggestions(troot):
            print(f"  {y}      建议把 {design} 的 CASE_STATE {key} 改为 "
                  f"{new_path}{rst}")
        for design, path in _artifact_not_in_ledger_items(ledger_advice):
            print(f"  {y}      {design}: {path} 存在但未在 run_ledger.jsonl "
                  f"的 outputs 中记录，账本重哈希未覆盖该产物。{rst}")

    print()
    if total_flags == 0 and not ledger_hard:
        print(f"{g}自检通过：没有会被标红的项。可以提交。{rst}")
        print("提示：如实记录的失败/阻塞不会被扣分；只有无证据的 PASS、伪造、"
              "切平台(红线3)、抄袭(跨提交撞哈希)才会出问题。")
    else:
        parts: list[str] = []
        if total_flags:
            parts.append(f"{total_flags} 个 stage 标红")
        if ledger_hard:
            parts.append(f"{len(ledger_hard)} 个账本严重问题")
        desc = "、".join(parts)
        print(f"{r}发现 {desc}，建议修好再提交。{rst}")
        print("修不掉也没关系——把真实情况如实记成 FAILED_*/BLOCKED_* 即可，"
              "不要把它改写成无证据的 PASS。")
    return 0


def _case_state_path_suggestions(teaching_root: Path) -> list[tuple[str, str, str]]:
    suggestions: list[tuple[str, str, str]] = []
    cases_dir = teaching_root / "cases"
    if not cases_dir.is_dir():
        return suggestions
    for design_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        cs = parse_case_state(design_dir / "CASE_STATE.md")
        for key, subdir in _CASE_STATE_ARTIFACT_SUBDIR.items():
            val = cs.get(key, "").strip()
            if not val.startswith("<repo>/"):
                continue
            basename = val.rsplit("/", 1)[-1]
            if not basename:
                continue
            expected = design_dir / subdir / basename
            if expected.is_file():
                suggestions.append((
                    design_dir.name,
                    key,
                    f"<teaching_root>/cases/{design_dir.name}/{subdir}/{basename}",
                ))
    return suggestions


def _artifact_not_in_ledger_items(notes: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    marker = "<teaching_root>/cases/"
    for note in notes:
        if not note.startswith("artifact_not_in_ledger:"):
            continue
        path = note.split(":", 1)[1]
        design = "UNKNOWN"
        if path.startswith(marker):
            rest = path[len(marker):]
            if "/" in rest:
                design = rest.split("/", 1)[0]
        items.append((design, path))
    return items


if __name__ == "__main__":
    sys.exit(main())
