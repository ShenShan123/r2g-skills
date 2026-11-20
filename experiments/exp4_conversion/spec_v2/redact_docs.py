#!/usr/bin/env python3
"""Exp 4 v2: copy def-graph's own public docs for the LLM, stripping ONLY passages that name R2G
script paths or commands (PROTOCOL_v2.md section 3). Nothing else is edited.

Rules (applied in order; every application is counted in REDACTION_LOG.json):
  1. a fenced code block that contains a command or an R2G script path (bash/python/pip invocation,
     `scripts/`, `tools/`, a *.py / *.sh / *.tcl name) is replaced by the line
     `[R2G command block removed]` inside the fence;
  2. an inline `code span` naming such a path/command -> `[R2G code reference removed]`;
  3. a bare path `scripts/...` / `tools/...` or a bare *.py / *.sh / *.tcl file name ->
     `[R2G code reference removed]`.
    redact_docs.py <def-graph skill dir> <out dir>
"""
import json
import re
import sys
from pathlib import Path

DOCS = ["SKILL.md", "references/graph-dataset.md", "references/feature-extraction.md",
        "references/label-extraction.md", "references/four-stage-dataset.md",
        "scripts/r2g2/upstream_docs/B_VIEW_DATASET_STRUCTURE.md"]
CODEISH = re.compile(r"scripts/|tools/|\b[\w.-]+\.(?:py|sh|tcl)\b|^\s*(?:bash|python3?|pip|export)\b|\bpython3? |\bbash |\bpip install", re.M)
# code spans: only an R2G path/file name or a shell/python invocation (env-knob names and settings stay)
SPANISH = re.compile(r"scripts/|tools/|\b[\w.-]+\.(?:py|sh|tcl)\b|^\s*(?:bash|python3?|pip)\s")
SPAN = re.compile(r"`[^`\n]*`")
BARE = re.compile(r"(?<![\w/`])(?:\.\./)*(?:scripts|tools)/[\w./*{}-]+|(?<![\w/`])[\w{}*-]+(?:/[\w{}*.-]+)*\.(?:py|sh|tcl)\b")
TAG = "[R2G code reference removed]"


def redact(text, log):
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if lines[i].lstrip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("```"):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if CODEISH.search(body):
                out += [lines[i], "[R2G command block removed]", lines[j] if j < len(lines) else "```"]
                log["code_blocks"] += 1
            else:
                out += lines[i:j + 1]
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out)

    def span(m):
        if SPANISH.search(m.group(0)[1:-1]):
            log["code_spans"] += 1
            return "`" + TAG + "`"
        return m.group(0)
    text = SPAN.sub(span, text)
    # bare references outside code spans
    parts = re.split(r"(`[^`\n]*`)", text)
    for k in range(0, len(parts), 2):
        parts[k], n = BARE.subn(TAG, parts[k])
        log["bare_refs"] += n
    return "".join(parts)


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    log = {}
    for rel in DOCS:
        lg = {"code_blocks": 0, "code_spans": 0, "bare_refs": 0}
        t = redact((src / rel).read_text(), lg)
        (dst / Path(rel).name).write_text(t)
        log[rel] = lg
    (dst / "REDACTION_LOG.json").write_text(json.dumps(log, indent=1))
    print(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
