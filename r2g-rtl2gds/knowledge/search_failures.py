#!/usr/bin/env python3
"""BM25 search over failure patterns and mined failure candidates.

Usage:
  search_failures.py <query> [--patterns <path>] [--candidates <path>] [--top-k N]

Builds a lightweight BM25 index from two sources:
  1. references/failure-patterns.md (structured ## sections)
  2. knowledge/failure_candidates.json (mined signatures)

Returns ranked results as JSON. No external dependencies.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import knowledge_db

_PATTERNS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md"
_CANDIDATES_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json"


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop short tokens."""
    return [t for t in re.split(r"[^a-z0-9_.-]+", text.lower()) if len(t) >= 2]


class BM25Index:
    """Minimal BM25 (Okapi BM25) implementation over a list of documents.

    Each document is {"id": str, "text": str}.
    """

    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.N = len(docs)

        self.doc_tfs: list[Counter] = []
        self.doc_lens: list[int] = []
        self.df: Counter = Counter()

        for doc in docs:
            tokens = _tokenize(doc["text"])
            tf = Counter(tokens)
            self.doc_tfs.append(tf)
            self.doc_lens.append(len(tokens))
            for term in tf:
                self.df[term] += 1

        self.avgdl = sum(self.doc_lens) / self.N if self.N > 0 else 1.0

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)

    def _score_doc(self, query_terms: list[str], idx: int) -> float:
        tf = self.doc_tfs[idx]
        dl = self.doc_lens[idx]
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = self._idf(term)
            freq = tf[term]
            num = freq * (self.k1 + 1)
            den = freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += idf * num / den
        return score

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        terms = _tokenize(query)
        if not terms:
            return []
        scored = []
        for i, doc in enumerate(self.docs):
            s = self._score_doc(terms, i)
            if s > 0:
                scored.append({"id": doc["id"], "score": round(s, 4), "text": doc["text"]})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


def parse_failure_patterns(path: Path) -> list[dict]:
    """Parse failure-patterns.md into a list of {"id": section_title, "text": section_body}."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    docs = []
    for section in sections[1:]:  # skip preamble before first ##
        lines = section.strip().split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        docs.append({"id": title, "text": f"{title} {body}"})
    return docs


def parse_failure_candidates(path: Path) -> list[dict]:
    """Parse failure_candidates.json into searchable documents."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    docs = []
    for c in data.get("candidates", []):
        sig = c.get("signature", "")
        parts = [
            sig,
            " ".join(c.get("stages", [])),
            " ".join(c.get("designs", [])),
            c.get("sample_detail") or "",
        ]
        docs.append({
            "id": f"mined:{sig}",
            "text": " ".join(parts),
        })
    return docs


def search(query: str,
           patterns_path: Path = _PATTERNS_PATH,
           candidates_path: Path = _CANDIDATES_PATH,
           top_k: int = 5) -> list[dict]:
    """Search failure knowledge base and return ranked results."""
    docs = parse_failure_patterns(patterns_path) + parse_failure_candidates(candidates_path)
    if not docs:
        return []
    index = BM25Index(docs)
    return index.search(query, top_k=top_k)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", help="Error message or failure description to search for")
    p.add_argument("--patterns", type=Path, default=_PATTERNS_PATH,
                   help="Path to failure-patterns.md")
    p.add_argument("--candidates", type=Path, default=_CANDIDATES_PATH,
                   help="Path to failure_candidates.json")
    p.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    args = p.parse_args()

    results = search(args.query, patterns_path=args.patterns,
                     candidates_path=args.candidates, top_k=args.top_k)
    print(json.dumps(results, indent=2))
    if not results:
        print("No matching failure patterns found.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
