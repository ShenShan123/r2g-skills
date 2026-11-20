"""Build and verify a proposed patch without editing the frozen implementation."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE = Path('/home/yangao/r2g-exp2-rerun/r2g-skills/rtl-acquire/vendor/rtl-expander/scripts/recover_license_evidence.py')
TARGET = 'r2g-skills/rtl-acquire/vendor/rtl-expander/scripts/recover_license_evidence.py'
original = SOURCE.read_text()
old = '''    if "redistribution and use in source and binary forms" in lower:
        found.add("BSD-3-Clause")'''
new = '''    if (
        "redistribution and use in source and binary forms" in lower
        and "this software is provided by the copyright holders and contributors" in lower
    ):
        # The redistribution preamble is shared by BSD-2 and BSD-3. Only
        # BSD-3 includes the non-endorsement clause; preserve that distinction
        # when combining full-text evidence with provider SPDX metadata.
        non_endorsement = (
            "neither the name of" in lower
            and "may be used to endorse or promote" in lower
        )
        found.add("BSD-3-Clause" if non_endorsement else "BSD-2-Clause")'''
assert original.count(old) == 1
patched = original.replace('    lower = text.lower()\n', '    lower = " ".join(text.lower().split())\n', 1).replace(old, new, 1)
diff = ''.join(difflib.unified_diff(original.splitlines(True), patched.splitlines(True),
                                 fromfile='a/' + TARGET, tofile='b/' + TARGET))
(HERE / 'proposed_bsd_license_fix.patch').write_text(diff)


def load_recovery(source):
    # Execute only the pure recovery code; omit CLI and database imports.
    tree = ast.parse(source)
    functions = {'normalize_identifier', 'detect_full_text', 'bounded_files', 'recover'}
    constants = {'SCHEMA', 'LICENSE_NAMES', 'README_NAMES', 'SPDX_RE', 'IDS'}
    selected = [n for n in tree.body if
                isinstance(n, ast.FunctionDef) and n.name in functions or
                isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)]
    namespace = {'Path': Path, 're': re, 'Any': Any}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


before, after = load_recovery(original), load_recovery(patched)
samples = json.loads((HERE / 'control_traces.json').read_text())['license_text_samples']
results = []
for sample in samples:
    text = sample['text']
    assert hashlib.sha256(text.encode()).hexdigest() == sample['sha256']
    assert before['detect_full_text'](text) == {'BSD-3-Clause'}
    assert after['detect_full_text'](text) == {'BSD-2-Clause'}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'LICENSE').write_text(text)
        old_evidence = before['recover'](root, 'BSD-2-Clause')
        fixed = after['recover'](root, 'BSD-2-Clause')
        assert old_evidence['identifiers'] == ['BSD-2-Clause', 'BSD-3-Clause']
        assert fixed['identifiers'] == ['BSD-2-Clause']
        assert fixed['license_files'] == ['LICENSE']
        assert fixed['release_policy'] == 'PUBLIC_EXPORT_ALLOWED'
        assert fixed['confidence'] == 'HIGH'
    results.append({'repo_url': sample['repo_url'], 'license_sha256': sample['sha256'],
                    'old_identifiers': old_evidence['identifiers'], 'proposed_identifiers': fixed['identifiers']})

base = samples[0]['text']
endorsement = 'Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.\n'
three_clause = base + '\n' + endorsement
assert after['detect_full_text'](three_clause) == {'BSD-3-Clause'}
assert after['detect_full_text'](three_clause.replace('may be used', 'may\nbe\tused')) == {'BSD-3-Clause'}
assert after['detect_full_text']('redistribution and use in source and binary forms') == set()
for text in ('Permission is hereby granted, free of charge', 'Apache License Version 2.0',
             'ISC License', 'GNU General Public License Version 3'):
    assert before['detect_full_text'](text) == after['detect_full_text'](text)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    # Genuine evidence disagreement still produces multiple identifiers.
    (root / 'LICENSE').write_text(three_clause)
    mixed = after['recover'](root, 'BSD-2-Clause')
    assert mixed['identifiers'] == ['BSD-2-Clause', 'BSD-3-Clause']
    assert len(mixed['identifiers']) != 1  # Existing bridge still withholds declaration.
    (root / 'LICENSE').unlink()
    unknown = after['recover'](root)
    assert unknown['release_policy'] == 'QUARANTINE'
    metadata_only = after['recover'](root, 'BSD-2-Clause')
    assert metadata_only['release_policy'] == 'QUARANTINE'

assert SOURCE.read_text() == original
report = {'source_path': str(SOURCE), 'source_sha256': hashlib.sha256(original.encode()).hexdigest(),
          'source_unchanged': True, 'real_license_regressions': results,
          'additional_checks': ['BSD-3 retained', 'wrapped BSD-3 retained', 'fragment remains unresolved',
                                'MIT/Apache/ISC/GPL unchanged', 'genuine mixed evidence retained',
                                'unknown quarantined', 'metadata-only quarantined'],
          'limits': 'Isolated recovery and bridge identifier checks only; no synthesis or rescoring, no claim of extra accepted designs.'}
(HERE / 'patch_verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
