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

from verify_submission import verify_submission  # noqa: E402

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

    print("\n--- 账本 ---")
    if ledger_notes == ["ledger_absent"]:
        print(f"  {y}未发现 run_ledger.jsonl。"
              f"若本课程要求账本，请确认用 run_stage.sh 跑流程（它会自动写账本）。{rst}")
    elif ledger_notes:
        print(f"  {r}账本问题：{', '.join(ledger_notes)}{rst}")
    else:
        print(f"  {g}账本链校验通过。{rst}")

    print()
    if total_flags == 0 and (not ledger_notes or ledger_notes == ["ledger_absent"]):
        print(f"{g}自检通过：没有会被标红的项。可以提交。{rst}")
        print("提示：如实记录的失败/阻塞不会被扣分；只有无证据的 PASS、伪造、"
              "切平台(红线3)、抄袭(跨提交撞哈希)才会出问题。")
    else:
        print(f"{r}发现 {total_flags} 个会被标红的项，建议修好再提交。{rst}")
        print("修不掉也没关系——把真实情况如实记成 FAILED_*/BLOCKED_* 即可，"
              "不要把它改写成无证据的 PASS。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
