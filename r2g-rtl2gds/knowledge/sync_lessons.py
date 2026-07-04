#!/usr/bin/env python3
"""One-way prose -> `lessons` table sync (spec 2026-06-09 §4.4).

Parses `r2g-lesson:` HTML-comment front-matter from each ## section of
failure-patterns.md / signoff-fixing.md, upserts a lessons row keyed by lesson_id,
and back-fills evidence_runs_json by matching the symptom trigger against
run_violations.symptom_id / signature_json. Prose is never modified. Idempotent.
"""
from __future__ import annotations

import hashlib
import fnmatch
import json
import re
from pathlib import Path

import knowledge_db
import symptom

# Match h2-h4 headings: lessons usually live at the granular ###/#### sub-section
# (e.g. "### LVS symmetric-matcher residual"), not only top-level ## sections.
_SECTION_RE = re.compile(r"^#{2,4} (.+)$", re.MULTILINE)
_FRONT_RE = re.compile(r"<!--\s*r2g-lesson:(.*?)-->", re.DOTALL)

_DEFAULT_DOCS = [
    knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md",
    knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "signoff-fixing.md",
]


def _parse_frontmatter(block: str) -> dict:
    """Parse the lightweight key: value front-matter. Values may be JSON-ish
    ({...}, [...], or "*"/bare scalars). Tolerant: unparsable value -> string."""
    out: dict = {}
    for line in block.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        try:
            out[key] = json.loads(_jsonify(val))
        except (json.JSONDecodeError, ValueError):
            out[key] = val.strip('"')
    return out


def _jsonify(val: str) -> str:
    # Turn {check: lvs, platform: "*"} and [lvs_same_nets_seed, b] into strict JSON.
    if val.startswith("{") or val.startswith("["):
        v = re.sub(r"([{\[,]\s*)([A-Za-z_][\w]*)(\s*):", r'\1"\2"\3:', val)  # quote keys
        # quote bare-word dict scalars, but NOT the JSON literals true/false/null
        # (a `\b` after the literal lets `nullable`/`trueish` still be quoted).
        v = re.sub(r":(\s*)(?!(?:true|false|null)\b)([A-Za-z_][\w/.*-]*)(\s*[,}\]])",
                   r':\1"\2"\3', v)
        # quote bare-word LIST elements (preceded by [ or , ; followed by , or ]),
        # again excluding the JSON literals. lookbehind/lookahead don't consume the
        # separators, so consecutive elements each match (e.g. [a, b] -> ["a", "b"]).
        v = re.sub(r"(?<=[\[,])(\s*)(?!(?:true|false|null)\b)([A-Za-z_][\w/.*-]*)(\s*)(?=[,\]])",
                   r'\1"\2"\3', v)
        return v
    if val in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", val):
        return val
    return json.dumps(val.strip('"'))


def _iter_sections(text: str):
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        yield title, body


def _evidence_for(conn, trigger: dict) -> list[str]:
    """Find run_ids whose run_violations symptom matches the trigger (check/class;
    platform '*' matches any, else exact)."""
    rows = conn.execute(
        "SELECT run_id, platform, signature_json FROM run_violations "
        "WHERE symptom_id IS NOT NULL").fetchall()
    want_platform = trigger.get("platform", "*")
    out = []
    for run_id, plat, sigj in rows:
        try:
            sig = json.loads(sigj or "{}")
        except json.JSONDecodeError:
            continue
        if trigger.get("check") and sig.get("check") != trigger["check"]:
            continue
        # Glob class match, mirroring search_failures.lessons_for_symptom
        # ("*_ANTENNA" must collect evidence across METAL1..5_ANTENNA).
        if trigger.get("class") and not fnmatch.fnmatchcase(
                str(sig.get("class")), str(trigger["class"])):
            continue
        if want_platform not in ("*", None) and plat != want_platform:
            continue
        out.append(run_id)
    return out


def sync(conn, patterns_path: Path | None = None) -> int:
    docs = [Path(patterns_path)] if patterns_path else _DEFAULT_DOCS
    n = 0
    for doc in docs:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for title, body in _iter_sections(text):
            fm = _FRONT_RE.search(body)
            if not fm:
                continue
            meta = _parse_frontmatter(fm.group(1))
            lid = meta.get("id")
            if not lid:
                continue
            trigger = meta.get("trigger") or {}
            prose = _FRONT_RE.sub("", body).strip()[:400]
            content_hash = hashlib.sha1(body.encode("utf-8")).hexdigest()
            evidence = _evidence_for(conn, trigger)
            conn.execute(
                "INSERT INTO lessons (lesson_id, source_doc, section_title, status, "
                " symptom_trigger_json, strategy_ids_json, prose_excerpt, "
                " evidence_runs_json, content_hash, synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now')) "
                "ON CONFLICT(lesson_id) DO UPDATE SET "
                "  status=excluded.status, symptom_trigger_json=excluded.symptom_trigger_json, "
                "  strategy_ids_json=excluded.strategy_ids_json, prose_excerpt=excluded.prose_excerpt, "
                "  evidence_runs_json=excluded.evidence_runs_json, "
                "  content_hash=excluded.content_hash, synced_at=datetime('now')",
                (lid, str(doc), title, meta.get("status", "active"),
                 json.dumps(trigger, sort_keys=True),
                 json.dumps(meta.get("strategy_ids") or []), prose,
                 json.dumps(evidence), content_hash))
            n += 1
    conn.commit()
    return n


def main() -> int:
    conn = knowledge_db.connect(knowledge_db.DEFAULT_DB_PATH)
    knowledge_db.ensure_schema(conn)
    n = sync(conn)
    print(f"Synced {n} lesson(s).")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
