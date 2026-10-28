#!/usr/bin/env python3
"""Encode negative-index names for the pinned OpenSTA SPEF lexer.

No port/net is renamed: decoding the new spelling yields the original name.
No RC entry, numerical value, connection, or name-map index is changed.
The original extraction is retained next to the normalized file.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

NEGATIVE_INDEX = re.compile(r'(?<!\\)\[\\?-(\d+)\]')


def decode(token):
    return re.sub(r'\\(.)', r'\1', token)


def normalize(text):
    mappings = {}
    section = None
    lines = []
    for line in text.splitlines(keepends=True):
        tokens = list(re.finditer(r'\S+', line))
        if not tokens:
            lines.append(line)
            continue
        head = tokens[0].group()
        if head in ('*NAME_MAP', '*PORTS', '*CONN', '*CAP', '*RES', '*END'):
            section = head
        if head == '*D_NET':
            fields = [1]
            section = '*D_NET'
        elif section == '*NAME_MAP' and re.fullmatch(r'\*\d+', head):
            fields = [1]
        elif section == '*PORTS' and not head.startswith('*'):
            fields = [0]
        elif section == '*CONN' and head in ('*P', '*I'):
            fields = [1]
        elif section in ('*CAP', '*RES') and head.isdigit():
            fields = list(range(1, len(tokens)-1))
        else:
            fields = []
        for index in reversed(fields):
            token = tokens[index]
            old = token.group()
            new = NEGATIVE_INDEX.sub(lambda m: r'\[\-' + m.group(1) + r'\]', old)
            if old == new:
                continue
            if decode(old) != decode(new):
                raise ValueError('normalization changed decoded name')
            mappings[old] = new
            line = line[:token.start()] + new + line[token.end():]
        lines.append(line)
    result = ''.join(lines)
    # Compare every token, not merely the symbols that triggered the repair.
    before, after = text.split(), result.split()
    if len(before) != len(after) or any(decode(a) != decode(b) for a,b in zip(before,after)):
        raise ValueError('SPEF contents changed beyond escape representation')
    return result, mappings


def main():
    p = argparse.ArgumentParser(); p.add_argument('spef', type=Path); args = p.parse_args()
    original = args.spef.read_text()
    normalized, mappings = normalize(original)
    backup = args.spef.with_suffix(args.spef.suffix+'.original')
    # Never overwrite prior extraction evidence, including a rerun's new output.
    if backup.exists():
        raise FileExistsError('normalization evidence already exists: '+str(backup))
    backup.write_text(original)
    evidence = dict(original_sha256=hashlib.sha256(original.encode()).hexdigest(),
                    normalized_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
                    mappings=mappings, decoded_tokens_identical=True,
                    modifies_connectivity=False, modifies_rc_values=False)
    args.spef.with_suffix('.name_encoding.json').write_text(json.dumps(evidence,indent=2)+'\n')
    temporary = args.spef.with_suffix('.encoding.tmp')
    temporary.write_text(normalized); temporary.replace(args.spef)
    print('SPEF_NAME_ENCODING', json.dumps(mappings,sort_keys=True))


if __name__ == '__main__':
    main()
